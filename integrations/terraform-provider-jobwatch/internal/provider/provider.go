// Package provider implements the JobWatch Terraform provider (R64).
package provider

import (
	"context"
	"os"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/wecrew/terraform-provider-jobwatch/internal/client"
)

type jobwatchProvider struct{ version string }

type providerModel struct {
	APIURL types.String `tfsdk:"api_url"`
	APIKey types.String `tfsdk:"api_key"`
}

func New(version string) func() provider.Provider {
	return func() provider.Provider { return &jobwatchProvider{version: version} }
}

func (p *jobwatchProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = "jobwatch"
	resp.Version = p.version
}

func (p *jobwatchProvider) Schema(_ context.Context, _ provider.SchemaRequest, resp *provider.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Manage WeCrew JobWatch jobs and maintenance windows.",
		Attributes: map[string]schema.Attribute{
			"api_url": schema.StringAttribute{Optional: true,
				Description: "JobWatch app URL, e.g. https://app.example.com. Defaults to JOBWATCH_API_URL."},
			"api_key": schema.StringAttribute{Optional: true, Sensitive: true,
				Description: "API key (Settings → API keys; role devops or higher). Defaults to JOBWATCH_API_KEY."},
		},
	}
}

func (p *jobwatchProvider) Configure(ctx context.Context, req provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	var cfg providerModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &cfg)...)
	if resp.Diagnostics.HasError() {
		return
	}
	url, key := os.Getenv("JOBWATCH_API_URL"), os.Getenv("JOBWATCH_API_KEY")
	if !cfg.APIURL.IsNull() && !cfg.APIURL.IsUnknown() {
		url = cfg.APIURL.ValueString()
	}
	if !cfg.APIKey.IsNull() && !cfg.APIKey.IsUnknown() {
		key = cfg.APIKey.ValueString()
	}
	if url == "" || key == "" {
		resp.Diagnostics.AddError("JobWatch provider is not configured",
			"Set api_url and api_key in the provider block, or JOBWATCH_API_URL and JOBWATCH_API_KEY in the environment.")
		return
	}
	c := client.New(url, key, p.version)
	resp.ResourceData = c
	resp.DataSourceData = c
}

func (p *jobwatchProvider) Resources(_ context.Context) []func() resource.Resource {
	return []func() resource.Resource{NewJobResource, NewMaintenanceResource}
}

func (p *jobwatchProvider) DataSources(_ context.Context) []func() datasource.DataSource { return nil }
