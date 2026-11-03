.PHONY: all build test clean install

BIN_DIR := bin
BINARY := $(BIN_DIR)/lobectl
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)

all: test build

build:
	@mkdir -p $(BIN_DIR)
	go build -ldflags "-X main.version=$(VERSION)" -o $(BINARY) ./cmd/lobectl

test:
	go test -v ./...

install: build
	install -d $(HOME)/.local/bin
	install $(BINARY) $(HOME)/.local/bin/lobectl

clean:
	rm -rf $(BIN_DIR)
