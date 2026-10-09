// Minimal HTTP load generator: C workers hammer GET {base}/ping/{token} for D seconds with random
// tokens, then report status counts and latency percentiles. Stdlib only.
package main

import (
	"bufio"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"sort"
	"sync"
	"time"
)

func main() {
	base := flag.String("base", "http://127.0.0.1:18010", "ingest base URL")
	c := flag.Int("c", 50, "concurrent workers")
	d := flag.Duration("d", 20*time.Second, "duration")
	tf := flag.String("tokens", "tokens.txt", "token file")
	flag.Parse()
	f, _ := os.Open(*tf)
	var toks []string
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		toks = append(toks, sc.Text())
	}
	tr := &http.Transport{MaxIdleConnsPerHost: *c, MaxIdleConns: *c}
	cl := &http.Client{Transport: tr, Timeout: 10 * time.Second}
	var mu sync.Mutex
	codes := map[int]int{}
	var lat []time.Duration
	end := time.Now().Add(*d)
	var wg sync.WaitGroup
	for i := 0; i < *c; i++ {
		wg.Add(1)
		go func(seed int64) {
			defer wg.Done()
			r := rand.New(rand.NewSource(seed))
			var local []time.Duration
			lc := map[int]int{}
			for time.Now().Before(end) {
				t0 := time.Now()
				resp, err := cl.Get(*base + "/ping/" + toks[r.Intn(len(toks))])
				code := 0
				if err == nil {
					code = resp.StatusCode
					io.Copy(io.Discard, resp.Body) // drain so the connection is reused (keep-alive)
					resp.Body.Close()
				}
				lc[code]++
				local = append(local, time.Since(t0))
			}
			mu.Lock()
			for k, v := range lc {
				codes[k] += v
			}
			lat = append(lat, local...)
			mu.Unlock()
		}(int64(i))
	}
	wg.Wait()
	sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
	n := len(lat)
	p := func(q float64) time.Duration { return lat[int(float64(n-1)*q)] }
	fmt.Printf("requests=%d rps=%.0f codes=%v p50=%v p95=%v p99=%v max=%v\n", n, float64(n)/d.Seconds(), codes,
		p(.50).Round(time.Millisecond/10), p(.95).Round(time.Millisecond/10), p(.99).Round(time.Millisecond/10), lat[n-1].Round(time.Millisecond))
}
