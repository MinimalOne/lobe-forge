.PHONY: all build test clean install

BIN_DIR := bin
BINARY := $(BIN_DIR)/lobectl

all: test build

build:
	@mkdir -p $(BIN_DIR)
	go build -o $(BINARY) ./cmd/lobectl

test:
	go test -v ./...

install: build
	install -d $(HOME)/.local/bin
	install $(BINARY) $(HOME)/.local/bin/lobectl

clean:
	rm -rf $(BIN_DIR)
