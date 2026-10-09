FROM golang:1.23-alpine AS build
WORKDIR /src
COPY . .
# No go.sum is committed (k8s.io modules were unreachable where this was written); tidy resolves it.
# Commit the generated go.sum from a networked machine to make this build reproducible.
RUN go mod tidy && go mod download
RUN CGO_ENABLED=0 go build -ldflags "-s -w" -o /cronsentinel-k8s ./cmd/cronsentinel-k8s
FROM gcr.io/distroless/static:nonroot
COPY --from=build /cronsentinel-k8s /cronsentinel-k8s
USER nonroot
ENTRYPOINT ["/cronsentinel-k8s"]
