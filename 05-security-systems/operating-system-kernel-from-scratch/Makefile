CC ?= gcc
CFLAGS ?= -std=gnu11 -Wall -Wextra -O2 -Isrc
LD ?= ld

TEST_BIN := build/tests

all: $(TEST_BIN)

build:
	mkdir -p build

test: $(TEST_BIN)
	$(TEST_BIN)

$(TEST_BIN): tests/test_main.c src/kernel.c | build
	$(CC) $(CFLAGS) $^ -o $@

# Best-effort freestanding image. Needs a 32-bit toolchain and GRUB to boot.
image: build/kernel.elf

build/boot.o: src/boot.S | build
	$(CC) -m32 -ffreestanding -c $< -o $@

build/kernel.o: src/kernel.c | build
	$(CC) -m32 -ffreestanding -fno-pic -fno-stack-protector -c $< -o $@

build/kernel.elf: build/boot.o build/kernel.o src/linker.ld
	$(LD) -m elf_i386 -T src/linker.ld -o $@ build/boot.o build/kernel.o

clean:
	rm -rf build

.PHONY: all test image clean
