package provider

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/setdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/setplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/wecrew/terraform-provider-jobwatch/internal/client"
)

type maintenanceResource struct{ c *client.Client }

type maintenanceModel struct {
	ID             types.String `tfsdk:"id"`
	Name           types.String `tfsdk:"name"`
	StartsAt       types.String `tfsdk:"starts_at"`
	EndsAt         types.String `tfsdk:"ends_at"`
	Timezone       types.String `tfsdk:"timezone"`
	Repeat         types.String `tfsdk:"repeat"`
	Weekdays       types.Set    `tfsdk:"weekdays"`
	Tags           types.Set    `tfsdk:"tags"`
	JobIDs         types.Set    `tfsdk:"job_ids"`
	EnvironmentIDs types.Set    `tfsdk:"environment_ids"`
	RRule          types.String `tfsdk:"rrule"`
}

func NewMaintenanceResource() resource.Resource { return &maintenanceResource{} }

func (r *maintenanceResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_maintenance_window"
}

func (r *maintenanceResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	replace := []planmodifier.String{stringplanmodifier.RequiresReplace()}
	emptySet := setdefault.StaticValue(types.SetValueMust(types.StringType, []attr.Value{}))
	replaceSet := []planmodifier.Set{setplanmodifier.RequiresReplace()}
	resp.Schema = schema.Schema{
		Description: "A planned quiet period: runs due inside it are skipped and do not alert. " +
			"Windows cannot be edited in place; any change replaces the window.",
		Attributes: map[string]schema.Attribute{
			"id":        schema.StringAttribute{Computed: true, PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()}},
			"name":      schema.StringAttribute{Required: true, PlanModifiers: replace},
			"starts_at": schema.StringAttribute{Required: true, PlanModifiers: replace, Description: "RFC 3339, e.g. 2026-10-11T02:00:00+05:30"},
			"ends_at":   schema.StringAttribute{Required: true, PlanModifiers: replace, Description: "RFC 3339; for repeating windows, the end of the first occurrence"},
			"timezone": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("UTC"), PlanModifiers: replace,
				Description: "Repeating windows keep this zone's local time across daylight-saving changes."},
			"repeat": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("none"), PlanModifiers: replace,
				Description: "none, daily or weekly"},
			"weekdays": schema.SetAttribute{ElementType: types.StringType, Optional: true, Computed: true, Default: emptySet, PlanModifiers: replaceSet,
				Description: "For weekly windows: MO TU WE TH FR SA SU"},
			"tags": schema.SetAttribute{ElementType: types.StringType, Optional: true, Computed: true, Default: emptySet, PlanModifiers: replaceSet,
				Description: "Only jobs with any of these tags. Leave tags, job_ids and environment_ids empty for every job."},
			"job_ids":         schema.SetAttribute{ElementType: types.StringType, Optional: true, Computed: true, Default: emptySet, PlanModifiers: replaceSet},
			"environment_ids": schema.SetAttribute{ElementType: types.StringType, Optional: true, Computed: true, Default: emptySet, PlanModifiers: replaceSet},
			"rrule":           schema.StringAttribute{Computed: true, PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()}},
		},
	}
}

func (r *maintenanceResource) Configure(_ context.Context, req resource.ConfigureRequest, _ *resource.ConfigureResponse) {
	if req.ProviderData != nil {
		r.c = req.ProviderData.(*client.Client)
	}
}

// sameInstant keeps the configured spelling of a time when the API returns the same instant in UTC
// (otherwise "+05:30" vs "Z" would plan a replacement on every run).
func sameInstant(configured, api string) bool {
	a, err1 := time.Parse(time.RFC3339, configured)
	b, err2 := time.Parse(time.RFC3339, api)
	return err1 == nil && err2 == nil && a.Equal(b)
}

// repeatFromRRule turns the stored rule back into repeat + weekdays so real drift shows up.
func repeatFromRRule(rr *string) (string, []string) {
	if rr == nil || *rr == "" {
		return "none", []string{}
	}
	repeat, days := "none", []string{}
	for _, part := range strings.Split(*rr, ";") {
		k, v, _ := strings.Cut(part, "=")
		switch strings.ToUpper(k) {
		case "FREQ":
			repeat = strings.ToLower(v)
		case "BYDAY":
			days = strings.Split(strings.ToUpper(v), ",")
		}
	}
	return repeat, days
}

func (r *maintenanceResource) apply(ctx context.Context, m *client.Maintenance, st *maintenanceModel) {
	st.ID = types.StringValue(m.ID)
	st.Name = types.StringValue(m.Name)
	if !sameInstant(st.StartsAt.ValueString(), m.StartsAt) {
		st.StartsAt = types.StringValue(m.StartsAt)
	}
	if !sameInstant(st.EndsAt.ValueString(), m.EndsAt) {
		st.EndsAt = types.StringValue(m.EndsAt)
	}
	st.Timezone = types.StringValue(m.TZ)
	st.RRule = types.StringPointerValue(m.RRule)
	repeat, days := repeatFromRRule(m.RRule)
	st.Repeat = types.StringValue(repeat)
	st.Weekdays, _ = types.SetValueFrom(ctx, types.StringType, days)
	st.Tags, _ = types.SetValueFrom(ctx, types.StringType, nonNil(m.Scope.Tags))
	st.JobIDs, _ = types.SetValueFrom(ctx, types.StringType, nonNil(m.Scope.JobIDs))
	st.EnvironmentIDs, _ = types.SetValueFrom(ctx, types.StringType, nonNil(m.Scope.EnvironmentIDs))
}

func (r *maintenanceResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan maintenanceModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	days := strings_(ctx, plan.Weekdays, &resp.Diagnostics)
	for i := range days {
		days[i] = strings.ToUpper(days[i])
	}
	in := client.Maintenance{Name: plan.Name.ValueString(), StartsAt: plan.StartsAt.ValueString(), EndsAt: plan.EndsAt.ValueString(),
		TZ: plan.Timezone.ValueString(), Repeat: plan.Repeat.ValueString(), Weekdays: days,
		Scope: client.MaintenanceScope{Tags: strings_(ctx, plan.Tags, &resp.Diagnostics), JobIDs: strings_(ctx, plan.JobIDs, &resp.Diagnostics),
			EnvironmentIDs: strings_(ctx, plan.EnvironmentIDs, &resp.Diagnostics)}}
	m, err := r.c.CreateMaintenance(ctx, in)
	if err != nil {
		resp.Diagnostics.AddError("Could not create the maintenance window", err.Error())
		return
	}
	r.apply(ctx, m, &plan)
	plan.Weekdays = keepConfiguredSet(ctx, plan.Weekdays, req, resp) // keep the user's case/order if equal
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func keepConfiguredSet(ctx context.Context, got types.Set, req resource.CreateRequest, resp *resource.CreateResponse) types.Set {
	var configured types.Set
	resp.Diagnostics.Append(req.Plan.GetAttribute(ctx, path.Root("weekdays"), &configured)...)
	a, b := strings_(ctx, configured, &resp.Diagnostics), strings_(ctx, got, &resp.Diagnostics)
	if len(a) != len(b) {
		return got
	}
	seen := map[string]bool{}
	for _, d := range b {
		seen[d] = true
	}
	for _, d := range a {
		if !seen[strings.ToUpper(d)] {
			return got
		}
	}
	return configured
}

func (r *maintenanceResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var st maintenanceModel
	resp.Diagnostics.Append(req.State.Get(ctx, &st)...)
	if resp.Diagnostics.HasError() {
		return
	}
	prevDays := st.Weekdays
	m, err := r.c.GetMaintenance(ctx, st.ID.ValueString())
	if errors.Is(err, client.ErrNotFound) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.AddError("Could not read the maintenance window", err.Error())
		return
	}
	r.apply(ctx, m, &st)
	if equalFold(strings_(ctx, prevDays, &resp.Diagnostics), strings_(ctx, st.Weekdays, &resp.Diagnostics)) {
		st.Weekdays = prevDays
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &st)...)
}

func equalFold(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	seen := map[string]bool{}
	for _, x := range b {
		seen[strings.ToUpper(x)] = true
	}
	for _, x := range a {
		if !seen[strings.ToUpper(x)] {
			return false
		}
	}
	return true
}

func (r *maintenanceResource) Update(_ context.Context, _ resource.UpdateRequest, resp *resource.UpdateResponse) {
	resp.Diagnostics.AddError("Maintenance windows cannot be edited", "Every attribute forces a replacement; this should not be reached.")
}

func (r *maintenanceResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var st maintenanceModel
	resp.Diagnostics.Append(req.State.Get(ctx, &st)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.c.DeleteMaintenance(ctx, st.ID.ValueString()); err != nil && !errors.Is(err, client.ErrNotFound) {
		resp.Diagnostics.AddError("Could not delete the maintenance window", err.Error())
	}
}

func (r *maintenanceResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resource.ImportStatePassthroughID(ctx, path.Root("id"), req, resp)
}
