package provider

import (
	"context"
	"errors"

	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/int64default"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/listdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/setdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/wecrew/terraform-provider-jobwatch/internal/client"
)

type jobResource struct{ c *client.Client }

type jobModel struct {
	ID                     types.String `tfsdk:"id"`
	WorkspaceID            types.String `tfsdk:"workspace_id"`
	Name                   types.String `tfsdk:"name"`
	Description            types.String `tfsdk:"description"`
	Schedule               types.String `tfsdk:"schedule"`
	Timezone               types.String `tfsdk:"timezone"`
	GraceSeconds           types.Int64  `tfsdk:"grace_seconds"`
	ExpectedRuntimeSeconds types.Int64  `tfsdk:"expected_runtime_seconds"`
	Tags                   types.Set    `tfsdk:"tags"`
	RunChecks              types.List   `tfsdk:"run_checks"`
	Paused                 types.Bool   `tfsdk:"paused"`
	PingURL                types.String `tfsdk:"ping_url"`
	PingTokenHint          types.String `tfsdk:"ping_token_hint"`
}

func NewJobResource() resource.Resource { return &jobResource{} }

func (r *jobResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_job"
}

func (r *jobResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	keep := []planmodifier.String{stringplanmodifier.UseStateForUnknown()}
	resp.Schema = schema.Schema{
		Description: "A scheduled job JobWatch expects to run. Report runs to `ping_url`, or let the agent discover the job.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{Computed: true, PlanModifiers: keep},
			"workspace_id": schema.StringAttribute{Optional: true, Computed: true,
				Description:   "Workspace for the job. Defaults to the organization's first workspace. Changing it replaces the job.",
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown(), stringplanmodifier.RequiresReplaceIfConfigured()}},
			"name":        schema.StringAttribute{Required: true},
			"description": schema.StringAttribute{Optional: true},
			"schedule": schema.StringAttribute{Optional: true,
				Description: "Cron expression, e.g. `0 2 * * *`. Omit for a job without a fixed schedule."},
			"timezone": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("UTC"),
				Description: "IANA time zone the schedule is in, e.g. `Asia/Kolkata`."},
			"grace_seconds": schema.Int64Attribute{Optional: true, Computed: true, Default: int64default.StaticInt64(300),
				Description: "How late a run may start before it counts as late."},
			"expected_runtime_seconds": schema.Int64Attribute{Optional: true,
				Description: "Runs still going after this long are flagged as running long."},
			"tags": schema.SetAttribute{ElementType: types.StringType, Optional: true, Computed: true,
				Default: setdefault.StaticValue(types.SetValueMust(types.StringType, []attr.Value{}))},
			"run_checks": schema.ListAttribute{ElementType: types.StringType, Optional: true, Computed: true,
				Default:       listdefault.StaticValue(types.ListValueMust(types.StringType, []attr.Value{})),
				PlanModifiers: []planmodifier.List{sameRunChecks{}},
				Description:   "Rules a successful run must also satisfy, e.g. `metric.size_bytes > 0`, `duration < 2h`."},
			"paused": schema.BoolAttribute{Optional: true, Computed: true, Default: booldefault.StaticBool(false)},
			"ping_url": schema.StringAttribute{Computed: true, Sensitive: true, PlanModifiers: []planmodifier.String{keepStoredPingURL{}},
				Description: "The job's ping URL. JobWatch shows it only once, at creation, so it lives only in Terraform state; " +
					"imported jobs have none (use New ping URL in the app)."},
			"ping_token_hint": schema.StringAttribute{Computed: true, PlanModifiers: keep,
				Description: "Last characters of the ping token, to recognise it without exposing it."},
		},
	}
}

// keepStoredPingURL: once a job exists, its ping URL never changes through Terraform (rotation happens in
// the app), so plan the stored value, even when it is empty after an import. UseStateForUnknown alone
// leaves an empty value "unknown", which shows every imported job as needing an update.
type keepStoredPingURL struct{}

func (keepStoredPingURL) Description(context.Context) string { return "keep the stored ping URL" }
func (keepStoredPingURL) MarkdownDescription(context.Context) string {
	return "keep the stored ping URL"
}
func (keepStoredPingURL) PlanModifyString(_ context.Context, req planmodifier.StringRequest, resp *planmodifier.StringResponse) {
	if !req.State.Raw.IsNull() { // the resource already exists
		resp.PlanValue = req.StateValue
	}
}

// sameRunChecks: run checks that differ only in spacing are the same rules (JobWatch collapses spaces),
// so plan no change. Matters after import, where state starts from the server's spelling.
type sameRunChecks struct{}

func (sameRunChecks) Description(context.Context) string {
	return "ignore whitespace-only differences in run checks"
}
func (sameRunChecks) MarkdownDescription(context.Context) string {
	return "ignore whitespace-only differences in run checks"
}
func (sameRunChecks) PlanModifyList(ctx context.Context, req planmodifier.ListRequest, resp *planmodifier.ListResponse) {
	if req.StateValue.IsNull() || req.PlanValue.IsNull() || req.PlanValue.IsUnknown() {
		return
	}
	var planned, state []string
	resp.Diagnostics.Append(req.PlanValue.ElementsAs(ctx, &planned, false)...)
	resp.Diagnostics.Append(req.StateValue.ElementsAs(ctx, &state, false)...)
	if sameChecks(planned, state) {
		resp.PlanValue = req.StateValue
	}
}

func (r *jobResource) Configure(_ context.Context, req resource.ConfigureRequest, _ *resource.ConfigureResponse) {
	if req.ProviderData != nil {
		r.c = req.ProviderData.(*client.Client)
	}
}

func strPtr(v types.String) *string {
	if v.IsNull() || v.IsUnknown() {
		return nil
	}
	s := v.ValueString()
	return &s
}

func intPtr(v types.Int64) *int64 {
	if v.IsNull() || v.IsUnknown() {
		return nil
	}
	n := v.ValueInt64()
	return &n
}

func strings_(ctx context.Context, v attr.Value, diags *diag.Diagnostics) []string {
	out := []string{}
	switch t := v.(type) {
	case types.Set:
		diags.Append(t.ElementsAs(ctx, &out, false)...)
	case types.List:
		diags.Append(t.ElementsAs(ctx, &out, false)...)
	}
	return out
}

// fromAPI writes API values into the model. Values the API never returns (ping URL after creation)
// are left as they are.
func fromAPI(ctx context.Context, j *client.Job, m *jobModel, diags *diag.Diagnostics) {
	m.ID = types.StringValue(j.ID)
	m.WorkspaceID = types.StringValue(j.WorkspaceID)
	m.Name = types.StringValue(j.Name)
	m.Description = types.StringPointerValue(j.Description)
	m.Schedule = types.StringPointerValue(j.ScheduleExpr)
	m.Timezone = types.StringValue(j.TZ)
	if j.GraceS != nil {
		m.GraceSeconds = types.Int64Value(*j.GraceS)
	}
	m.ExpectedRuntimeSeconds = types.Int64PointerValue(j.ExpectedRuntimeS)
	tags, d := types.SetValueFrom(ctx, types.StringType, nonNil(j.Tags))
	diags.Append(d...)
	m.Tags = tags
	checks, d := types.ListValueFrom(ctx, types.StringType, nonNil(j.Assertions))
	diags.Append(d...)
	m.RunChecks = checks
	if j.Paused != nil {
		m.Paused = types.BoolValue(*j.Paused)
	}
	if j.HeartbeatTokenHint != "" {
		m.PingTokenHint = types.StringValue(j.HeartbeatTokenHint)
	}
	if j.PingURL != nil {
		m.PingURL = types.StringValue(*j.PingURL)
	} else if m.PingURL.IsUnknown() {
		m.PingURL = types.StringNull()
	}
}

func nonNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

func (r *jobResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan jobModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	ws := plan.WorkspaceID.ValueString()
	if plan.WorkspaceID.IsNull() || plan.WorkspaceID.IsUnknown() {
		var err error
		if ws, err = r.c.DefaultWorkspace(ctx); err != nil {
			resp.Diagnostics.AddError("Could not find a workspace", err.Error())
			return
		}
	}
	paused := plan.Paused.ValueBool()
	grace := plan.GraceSeconds.ValueInt64()
	in := client.Job{WorkspaceID: ws, Name: plan.Name.ValueString(), Description: strPtr(plan.Description),
		ScheduleExpr: strPtr(plan.Schedule), TZ: plan.Timezone.ValueString(), GraceS: &grace,
		ExpectedRuntimeS: intPtr(plan.ExpectedRuntimeSeconds),
		Tags:             strings_(ctx, plan.Tags, &resp.Diagnostics), Assertions: strings_(ctx, plan.RunChecks, &resp.Diagnostics)}
	j, err := r.c.CreateJob(ctx, in)
	if err != nil {
		resp.Diagnostics.AddError("Could not create the job", err.Error())
		return
	}
	if paused { // the create endpoint has no paused field; apply it straight after
		if j, err = r.c.UpdateJob(ctx, j.ID, map[string]any{"paused": true}); err != nil {
			resp.Diagnostics.AddError("Job created but could not be paused", err.Error())
		}
	}
	token, url := j.HeartbeatToken, j.PingURL
	fromAPI(ctx, j, &plan, &resp.Diagnostics)
	if url == nil && token != nil {
		resp.Diagnostics.AddWarning("ping_url is empty",
			"The JobWatch server has no INGEST_PUBLIC_URL set, so it returned only the token. ping_token_hint identifies it.")
	}
	plan.RunChecks = keepPlannedList(plan.RunChecks, in.Assertions, ctx, &resp.Diagnostics)
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

// keepPlannedList keeps the configured spelling of run checks after apply (the API collapses extra
// spaces; reporting that as a change would make every apply "inconsistent").
func keepPlannedList(current types.List, planned []string, ctx context.Context, diags *diag.Diagnostics) types.List {
	got := strings_(ctx, current, diags)
	if len(got) != len(planned) {
		return current
	}
	v, d := types.ListValueFrom(ctx, types.StringType, planned)
	diags.Append(d...)
	return v
}

func (r *jobResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var st jobModel
	resp.Diagnostics.Append(req.State.Get(ctx, &st)...)
	if resp.Diagnostics.HasError() {
		return
	}
	j, err := r.c.GetJob(ctx, st.ID.ValueString())
	if errors.Is(err, client.ErrNotFound) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.AddError("Could not read the job", err.Error())
		return
	}
	prevChecks := strings_(ctx, st.RunChecks, &resp.Diagnostics)
	fromAPI(ctx, j, &st, &resp.Diagnostics)
	if sameChecks(prevChecks, nonNil(j.Assertions)) { // same rules, different spacing: not drift
		st.RunChecks = keepPlannedList(st.RunChecks, prevChecks, ctx, &resp.Diagnostics)
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &st)...)
}

func sameChecks(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if squash(a[i]) != squash(b[i]) {
			return false
		}
	}
	return true
}

func squash(s string) string {
	out, space := []rune{}, false
	for _, r := range s {
		if r == ' ' || r == '\t' {
			space = len(out) > 0
			continue
		}
		if space {
			out = append(out, ' ')
			space = false
		}
		out = append(out, r)
	}
	return string(out)
}

func (r *jobResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan, st jobModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	resp.Diagnostics.Append(req.State.Get(ctx, &st)...)
	if resp.Diagnostics.HasError() {
		return
	}
	checks := strings_(ctx, plan.RunChecks, &resp.Diagnostics)
	patch := map[string]any{
		"name": plan.Name.ValueString(), "description": strPtr(plan.Description), "schedule_expr": strPtr(plan.Schedule),
		"tz": plan.Timezone.ValueString(), "grace_s": plan.GraceSeconds.ValueInt64(),
		"expected_runtime_s": intPtr(plan.ExpectedRuntimeSeconds),
		"tags":               strings_(ctx, plan.Tags, &resp.Diagnostics), "assertions": checks, "paused": plan.Paused.ValueBool(),
	}
	j, err := r.c.UpdateJob(ctx, st.ID.ValueString(), patch)
	if err != nil {
		resp.Diagnostics.AddError("Could not update the job", err.Error())
		return
	}
	plan.PingURL, plan.PingTokenHint = st.PingURL, st.PingTokenHint
	fromAPI(ctx, j, &plan, &resp.Diagnostics)
	plan.RunChecks = keepPlannedList(plan.RunChecks, checks, ctx, &resp.Diagnostics)
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *jobResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var st jobModel
	resp.Diagnostics.Append(req.State.Get(ctx, &st)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.c.DeleteJob(ctx, st.ID.ValueString()); err != nil && !errors.Is(err, client.ErrNotFound) {
		resp.Diagnostics.AddError("Could not delete the job", err.Error())
	}
}

func (r *jobResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resource.ImportStatePassthroughID(ctx, path.Root("id"), req, resp)
	resp.Diagnostics.AddWarning("Imported jobs have no ping_url",
		"JobWatch never shows a ping URL again after creation. Keep using the existing URL, or choose New ping URL in the app.")
}
