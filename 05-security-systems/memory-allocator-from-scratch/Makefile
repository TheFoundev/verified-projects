CC ?= gcc
CXX ?= g++
CFLAGS ?= -std=c11 -Wall -Wextra -O2 -Isrc
CXXFLAGS ?= -std=c++17 -Wall -Wextra -O2 -Isrc

TEST_BIN := build/tests
SRC := $(wildcard src/*.c)
OBJ := $(patsubst src/%.c,build/%.o,$(SRC))

all: build/app

build:
	mkdir -p build

build/%.o: src/%.c | build
	$(CC) $(CFLAGS) -c $< -o $@

build/app: $(OBJ) | build
	$(CC) $(CFLAGS) $(OBJ) -o $@

test: $(TEST_BIN)
	$(TEST_BIN)

$(TEST_BIN): tests/test_main.c $(filter-out build/main.o,$(OBJ)) | build
	$(CC) $(CFLAGS) $^ -o $@

clean:
	rm -rf build

.PHONY: all test clean
