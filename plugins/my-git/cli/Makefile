# mygit build / test commands. Run `make help`.
BIN      := bin/mygit
PKG      := ./...
VERSION  ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
LDFLAGS  := -s -w -X main.version=$(VERSION)
GOFLAGS  ?=

.PHONY: help build install test test-race test-short cover lint vet fmt fmt-check tidy clean cross ci

help: ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n",$$1,$$2}'

build: ## build ./bin/mygit
	go build $(GOFLAGS) -trimpath -ldflags "$(LDFLAGS)" -o $(BIN) ./cmd/mygit

install: ## go install mygit into GOBIN
	go install -trimpath -ldflags "$(LDFLAGS)" ./cmd/mygit

test: ## run all tests
	go test $(GOFLAGS) $(PKG)

test-short: ## run unit tests only (skip tests that need real git)
	go test -short $(PKG)

test-race: ## run tests with the race detector
	go test -race $(PKG)

cover: ## tests with coverage report (coverage.out, coverage.html)
	go test -coverprofile=coverage.out $(PKG)
	go tool cover -func=coverage.out | tail -1
	go tool cover -html=coverage.out -o coverage.html

vet: ## go vet
	go vet $(PKG)

lint: vet fmt-check ## vet + gofmt check (+ golangci-lint if installed)
	@command -v golangci-lint >/dev/null && golangci-lint run || echo "golangci-lint not installed; skipped"

fmt: ## gofmt -w
	gofmt -s -w .

fmt-check: ## fail if gofmt would change files
	@out=$$(gofmt -s -l .); if [ -n "$$out" ]; then echo "gofmt needed:"; echo "$$out"; exit 1; fi

tidy: ## go mod tidy
	go mod tidy

cross: ## build for darwin/linux/windows (amd64, arm64) into dist/
	@for os in darwin linux windows; do for arch in amd64 arm64; do \
	  ext=$$( [ $$os = windows ] && echo .exe ); \
	  echo "dist/mygit_$${os}_$${arch}$$ext"; \
	  GOOS=$$os GOARCH=$$arch go build -trimpath -ldflags "$(LDFLAGS)" -o dist/mygit_$${os}_$${arch}$$ext ./cmd/mygit || exit 1; \
	done; done

ci: lint test-race build ## everything CI runs

clean: ## remove build output
	rm -rf bin dist coverage.out coverage.html
