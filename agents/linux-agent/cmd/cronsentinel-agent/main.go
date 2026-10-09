// cronsentinel-agent: discover + monitor scheduled jobs on a Linux host.
//
//	cronsentinel-agent enroll --server https://ingest.example.com --token <bootstrap> [--name host]
//	cronsentinel-agent run                       # daemon (systemd)
//	cronsentinel-agent exec [--job <token>] -- <cmd> [args...]   # cs-run wrapper
//	cronsentinel-agent discover                  # print discovered jobs as JSON
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"os/user"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/cronsentinel/linux-agent/internal/agent"
	"github.com/cronsentinel/linux-agent/internal/buffer"
	"github.com/cronsentinel/linux-agent/internal/discovery"
	"github.com/cronsentinel/linux-agent/internal/hostmetrics"
	"github.com/cronsentinel/linux-agent/internal/journal"
)

const defaultConfigPath = "/etc/cronsentinel/agent.json"

// spoolDir is where `cs-run` wrappers that cannot write the root-owned buffer (every crontab that
// does not belong to root) leave their events. install.sh creates it root:root 1733.
var spoolDir = func() string {
	if p := os.Getenv("CRONSENTINEL_SPOOL"); p != "" {
		return p
	}
	return "/var/lib/cronsentinel/spool"
}()

// currentUser is the account the wrapped job runs as. $USER is not set by every cron
// implementation, and discovery fingerprints jobs by the crontab owner's name, so read it from
// the uid instead of the environment.
func currentUser() string {
	if u, err := user.Current(); err == nil && u.Username != "" {
		return u.Username
	}
	return os.Getenv("USER")
}

// configPath is /etc/cronsentinel/agent.json unless CRONSENTINEL_CONFIG overrides it — for running
// the agent unprivileged (tests, a per-user install). The buffer path is inside the config itself.
var configPath = func() string {
	if p := os.Getenv("CRONSENTINEL_CONFIG"); p != "" {
		return p
	}
	return defaultConfigPath
}()

func hostID() string {
	if b, err := os.ReadFile("/etc/machine-id"); err == nil && len(b) > 8 {
		return strings.TrimSpace(string(b))
	}
	h, _ := os.Hostname()
	return h
}

func main() {
	if strings.HasSuffix(os.Args[0], "cs-run") { // symlink alias: cs-run [--job TOKEN] -- cmd
		os.Exit(cmdExec(os.Args[1:]))
	}
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: cronsentinel-agent enroll|run|exec|discover")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "enroll":
		cmdEnroll(os.Args[2:])
	case "run":
		cmdRun()
	case "exec":
		os.Exit(cmdExec(os.Args[2:]))
	case "discover":
		h := hostID()
		jobs := append(discovery.Crontabs(h), discovery.SystemdTimers(h)...)
		json.NewEncoder(os.Stdout).Encode(jobs)
	default:
		fmt.Fprintln(os.Stderr, "unknown command")
		os.Exit(2)
	}
}

func cmdEnroll(args []string) {
	fs := flag.NewFlagSet("enroll", flag.ExitOnError)
	server := fs.String("server", "", "ingest server URL")
	token := fs.String("token", "", "one-time bootstrap token")
	name := fs.String("name", "", "agent display name (default hostname)")
	insecure := fs.Bool("insecure", false, "skip TLS verification (dev only)")
	fs.Parse(args)
	if *server == "" || *token == "" {
		log.Fatal("--server and --token required")
	}
	if *name == "" {
		*name, _ = os.Hostname()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cfg, err := agent.Enroll(ctx, *server, *token, *name, hostID(), *insecure)
	if err != nil {
		log.Fatalf("enroll failed: %v", err)
	}
	os.MkdirAll(filepath.Dir(configPath), 0o750)
	if err := cfg.Save(configPath); err != nil {
		log.Fatalf("save config: %v", err)
	}
	fmt.Println("enrolled: agent_id =", cfg.AgentID, "→ config written to", configPath)
}

// exec: wrapper mode. Never needs the agent key: events go to the local buffer (root) or the spool
// (everyone else) and the daemon delivers them. The command always runs, monitored or not.
func cmdExec(args []string) int {
	fs := flag.NewFlagSet("exec", flag.ContinueOnError)
	jobToken := fs.String("job", "", "heartbeat token (optional; fingerprint used otherwise)")
	fs.Parse(args)
	argv := fs.Args()
	if len(argv) > 0 && argv[0] == "--" {
		argv = argv[1:]
	}
	if len(argv) == 0 {
		fmt.Fprintln(os.Stderr, "usage: cronsentinel-agent exec [--job TOKEN] -- cmd args...")
		return 2
	}
	cfg, err := agent.Load(configPath)
	if err != nil {
		// R39: unreadable config is the normal case for non-root crontabs (agent.json is 0600 root,
		// it holds the agent key). Only HostID matters here, and it is derived from machine-id, so
		// recompute it rather than fingerprinting with an empty host and having every event dropped.
		cfg = agent.Defaults()
	}
	if cfg.HostID == "" {
		cfg.HostID = hostID()
	}
	execName := fmt.Sprintf("%d-%d", time.Now().UnixNano(), os.Getpid())
	var push func(agent.Event) error
	var closer func()
	if buf, err := buffer.Open(cfg.BufferPath); err == nil {
		push, closer = func(ev agent.Event) error { return buf.Push(ev) }, func() { buf.Close() }
	} else if sp, serr := buffer.OpenSpool(spoolDir, execName); serr == nil {
		push, closer = func(ev agent.Event) error { return sp.Push(ev) }, func() { sp.Close() }
	} else {
		log.Printf("cronsentinel: cannot record this run (buffer: %v; spool: %v) — running unmonitored", err, serr)
		push, closer = func(agent.Event) error { return nil }, func() {}
	}
	defer closer()
	schedule := os.Getenv("CS_SCHEDULE")
	fp := discovery.Fingerprint(cfg.HostID, currentUser(), strings.Join(argv, " "), schedule)
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	return agent.Run(ctx, cfg, push, *jobToken, fp, schedule, argv)
}

// spoolAnnotate re-derives a spooled event's fingerprint from the *file owner*, so a local user can
// only report runs of their own commands (a forged fingerprint for root's backup job is replaced by
// one that matches nothing). Token-addressed events keep their token: the token is the credential.
func spoolAnnotate(hostID string) func([]byte, uint32) []byte {
	return func(raw []byte, uid uint32) []byte {
		var ev map[string]any
		if json.Unmarshal(raw, &ev) != nil {
			return raw
		}
		meta, _ := ev["meta"].(map[string]any)
		if meta == nil {
			meta = map[string]any{}
		}
		name := ""
		if u, err := user.LookupId(fmt.Sprint(uid)); err == nil {
			name = u.Username
		}
		cmd, _ := meta["command"].(string)
		sched, _ := meta["schedule"].(string)
		if cmd != "" {
			ev["fingerprint"] = discovery.Fingerprint(hostID, name, cmd, sched)
		} else {
			delete(ev, "fingerprint")
		}
		meta["spool_uid"] = fmt.Sprint(uid)
		meta["spool_user"] = name
		ev["meta"] = meta
		out, err := json.Marshal(ev)
		if err != nil {
			return raw
		}
		return out
	}
}

// spoolSettled: a spool file is imported once its writer has been quiet for a while. A running job
// touches its file with a progress event every 30 s, so 45 s of silence means the wrapper is done
// (or dead — its events are still worth delivering). Finished runs are imported at the next tick
// after that; the start→terminal pair still arrives together and in order.
func spoolSettled(fi os.FileInfo) bool { return time.Since(fi.ModTime()) > 45*time.Second }

func cmdRun() {
	cfg, err := agent.Load(configPath)
	if err != nil {
		log.Fatal(err)
	}
	buf, err := buffer.Open(cfg.BufferPath)
	if err != nil {
		log.Fatal(err)
	}
	defer buf.Close()
	client := agent.NewClient(cfg)
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	var wg sync.WaitGroup
	var tokMu sync.RWMutex
	tokens := map[string]string{}

	// 1. flusher: buffer → server, exponential backoff, resumes from committed offset
	wg.Add(1)
	go func() {
		defer wg.Done()
		backoff := cfg.FlushEvery
		for {
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
			}
			if n, err := buf.ImportSpool(spoolDir, spoolSettled, spoolAnnotate(cfg.HostID)); err != nil {
				log.Printf("spool import: %v", err)
			} else if n > 0 {
				log.Printf("spool: imported %d events", n)
			}
			lines, next, err := buf.Peek(cfg.BatchSize)
			if err != nil || len(lines) == 0 {
				backoff = cfg.FlushEvery
				continue
			}
			if err := client.SendEvents(ctx, lines); err != nil {
				if agent.IsFatal(err) {
					log.Fatalf("fatal: %v", err)
				}
				log.Printf("send failed (%d queued): %v", len(lines), err)
				if backoff < 5*time.Minute {
					backoff *= 2
				}
				continue
			}
			buf.Ack(next)
			backoff = cfg.FlushEvery
		}
	}()

	// 2. discovery
	wg.Add(1)
	go func() {
		defer wg.Done()
		for {
			jobs := append(discovery.Crontabs(cfg.HostID), discovery.SystemdTimers(cfg.HostID)...)
			if t, err := client.SendDiscovery(ctx, jobs); err != nil {
				log.Printf("discovery failed: %v", err)
			} else {
				tokMu.Lock()
				tokens = t
				tokMu.Unlock()
				log.Printf("discovery: %d jobs", len(jobs))
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(cfg.DiscoveryEvery):
			}
		}
	}()

	// 3. host metrics + liveness heartbeat
	wg.Add(1)
	go func() {
		defer wg.Done()
		hostmetrics.Collect("/") // prime cpu counters
		for {
			select {
			case <-ctx.Done():
				return
			case <-time.After(cfg.MetricsEvery):
			}
			m := hostmetrics.Collect("/")
			b, _ := json.Marshal(m)
			var mm map[string]any
			json.Unmarshal(b, &mm)
			if err := client.SendMetrics(ctx, mm); err != nil {
				log.Printf("metrics failed: %v", err)
			}
			_ = client.Heartbeat(ctx, cfg.MetricsEvery)
		}
	}()

	// 4. passive journal watcher for non-wrapped cron jobs
	if cfg.PassiveJournal {
		wg.Add(1)
		go func() {
			defer wg.Done()
			host, _ := os.Hostname()
			execIDs := map[int]string{}
			var mu sync.Mutex
			for ctx.Err() == nil {
				err := journal.Watch(ctx,
					func(st journal.Start) {
						if strings.Contains(st.Cmd, "cronsentinel-agent exec") || strings.Contains(st.Cmd, "cs-run") {
							return // wrapped jobs report themselves
						}
						fp := discovery.Fingerprint(cfg.HostID, st.User, st.Cmd, "")
						tokMu.RLock()
						tok := tokens[fp]
						tokMu.RUnlock()
						id := fmt.Sprintf("passive-%d-%d", st.PID, st.TS.UnixMilli())
						mu.Lock()
						execIDs[st.PID] = id
						mu.Unlock()
						buf.Push(agent.Event{Kind: "start", JobToken: tok, Fingerprint: fp, ExecutionID: id, Sequence: 0, AgentTS: st.TS, Host: host,
							Meta: map[string]string{"passive": "true", "command": st.Cmd, "agent_id": cfg.AgentID}})
					},
					func(st journal.Start, d time.Duration) {
						mu.Lock()
						id := execIDs[st.PID]
						delete(execIDs, st.PID)
						mu.Unlock()
						fp := discovery.Fingerprint(cfg.HostID, st.User, st.Cmd, "")
						tokMu.RLock()
						tok := tokens[fp]
						tokMu.RUnlock()
						ms := d.Milliseconds()
						// STUB: exit code unknowable in passive mode → reported as success with passive flag
						buf.Push(agent.Event{Kind: "success", JobToken: tok, Fingerprint: fp, ExecutionID: id, Sequence: 1, AgentTS: time.Now().UTC(), Host: host,
							DurationMS: &ms, Meta: map[string]string{"passive": "true", "exit_code_unknown": "true", "agent_id": cfg.AgentID}})
					})
				if err != nil && ctx.Err() == nil {
					log.Printf("journal watcher exited: %v (retrying in 30s)", err)
					time.Sleep(30 * time.Second)
				}
			}
		}()
	}

	log.Printf("cronsentinel-agent %s running (agent_id=%s)", agent.Version, cfg.AgentID)
	<-ctx.Done()
	wg.Wait()
}
