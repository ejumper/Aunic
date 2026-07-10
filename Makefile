STATICCHECK := go run honnef.co/go/tools/cmd/staticcheck@2025.1

.PHONY: all fmt check-fmt vet staticcheck test build

# Full local gate: formatting, vet, staticcheck, tests, and a build.
all: check-fmt vet staticcheck test build

# Rewrite all files with gofmt.
fmt:
	gofmt -w .

# Fail if any file is not gofmt-clean (list the offenders).
check-fmt:
	@out="$$(gofmt -l .)"; \
	if [ -n "$$out" ]; then \
		echo "gofmt needed:"; echo "$$out"; exit 1; \
	fi

vet:
	go vet ./...

# staticcheck via `go run` so no separate install is required.
staticcheck:
	$(STATICCHECK) ./...

test:
	go test ./...

build:
	go build ./...
