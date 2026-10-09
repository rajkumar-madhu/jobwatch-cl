VERSION ?= 0.1.0
LDFLAGS = -s -w
build:
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags "$(LDFLAGS)" -o dist/cronsentinel-agent-linux-amd64 ./cmd/cronsentinel-agent
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -ldflags "$(LDFLAGS)" -o dist/cronsentinel-agent-linux-arm64 ./cmd/cronsentinel-agent
test:
	go test ./...
lint:
	gofmt -l . && go vet ./...
release: build
	cp packaging/cronsentinel-agent.service dist/
	cp packaging/install.sh dist/   # R59: shipped with each release; it verifies the binaries against SHA256SUMS
	cd dist && sha256sum cronsentinel-agent-linux-amd64 cronsentinel-agent-linux-arm64 cronsentinel-agent.service > SHA256SUMS
