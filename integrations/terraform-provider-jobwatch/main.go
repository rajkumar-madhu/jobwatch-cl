// terraform-provider-jobwatch — manage WeCrew JobWatch jobs and maintenance windows as code (R64).
package main

import (
	"context"
	"flag"
	"log"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"

	"github.com/wecrew/terraform-provider-jobwatch/internal/provider"
)

var version = "dev" // set by -ldflags at release

func main() {
	var debug bool
	flag.BoolVar(&debug, "debug", false, "run with support for debuggers like delve")
	flag.Parse()
	err := providerserver.Serve(context.Background(), provider.New(version), providerserver.ServeOpts{
		Address: "registry.terraform.io/wecrew/jobwatch",
		Debug:   debug,
	})
	if err != nil {
		log.Fatal(err)
	}
}
