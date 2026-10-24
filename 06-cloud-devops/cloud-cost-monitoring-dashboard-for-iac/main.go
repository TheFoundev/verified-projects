package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
)

const examplePlan = `{
  "planned_values": {
    "root_module": {
      "resources": [
        {"type": "aws_instance", "name": "web", "values": {"instance_type": "t3.micro", "count": 2}},
        {"type": "aws_db_instance", "name": "db", "values": {"instance_class": "db.t3.small"}},
        {"type": "aws_s3_bucket", "name": "assets", "values": {"storage_gb": 500}}
      ]
    }
  }
}`

func main() {
	planPath := flag.String("plan", "", "path to terraform plan JSON")
	listen := flag.String("listen", "", "optional HTTP listen address")
	flag.Parse()

	data := []byte(examplePlan)
	if *planPath != "" {
		loaded, err := os.ReadFile(*planPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		data = loaded
	}
	resources, err := ParsePlan(data)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	summary := EstimateAll(resources)
	if *listen != "" {
		fmt.Println("serving cost API on", *listen)
		if err := http.ListenAndServe(*listen, Handler(summary)); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(summary); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
