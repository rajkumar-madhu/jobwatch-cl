package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"

	"github.com/cronsentinel/k8s-agent/internal/buffer"
	"github.com/cronsentinel/k8s-agent/internal/watcher"
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func main() {
	server := env("CS_SERVER", "")
	if server == "" {
		log.Fatal("CS_SERVER required")
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()

	agentID, agentKey := env("CS_AGENT_ID", ""), env("CS_AGENT_KEY", "")
	keyFile := env("CS_KEY_FILE", "/var/lib/cronsentinel/agent.key")
	if agentKey == "" {
		// R39: the key written at first enrollment was never read back. Every container restart
		// tried to enroll again with the (single-use) bootstrap token, was rejected, and crash-looped.
		if b, err := os.ReadFile(keyFile); err == nil {
			if parts := strings.SplitN(strings.TrimSpace(string(b)), "\n", 2); len(parts) == 2 && parts[1] != "" {
				agentID, agentKey = parts[0], parts[1]
				log.Println("using saved agent identity", agentID)
			}
		}
	}
	if agentKey == "" { // first boot: enroll with bootstrap token and persist to the state volume
		id, key, err := watcher.Enroll(ctx, server, env("CS_BOOTSTRAP_TOKEN", ""), env("CS_CLUSTER_NAME", "cluster"), env("CS_INSECURE", "false"))
		if err != nil {
			log.Fatalf("enroll: %v", err)
		}
		if id == "" || key == "" {
			log.Fatal("enroll: server returned no agent identity")
		}
		agentID, agentKey = id, key
		if err := os.WriteFile(keyFile, []byte(id+"\n"+key), 0o600); err != nil {
			log.Printf("WARNING: could not save agent key to %s (%v) — the next restart cannot re-enroll", keyFile, err)
		}
		log.Println("enrolled as", id, "— key saved to", keyFile, "(enable persistence or use existingSecret so it survives rescheduling)")
	}
	cfg, err := rest.InClusterConfig()
	if err != nil {
		log.Fatalf("in-cluster config: %v", err)
	}
	cs, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		log.Fatal(err)
	}
	var ns []string
	if v := env("CS_NAMESPACES", ""); v != "" {
		ns = strings.Split(v, ",")
	}
	client := watcher.NewClient(server, agentID, agentKey, env("CS_INSECURE", "false") == "true")
	log.Printf("cronsentinel-k8s %s starting (scope=%v)", watcher.Version, ns)
	w := watcher.New(cs, client, ns)
	if b, err := buffer.Open(env("CS_BUFFER_PATH", "/var/lib/cronsentinel/events.jsonl")); err == nil {
		defer b.Close()
		w.WithDiskQueue(b)
	} else {
		log.Printf("WARNING: disk queue unavailable (%v) — events are held in memory only", err)
	}
	w.Run(ctx, 2*time.Minute, 5*time.Second)
}
