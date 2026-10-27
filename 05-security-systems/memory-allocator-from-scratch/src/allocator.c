#include "allocator.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>

#define ARENA_BYTES (4u * 1024u * 1024u)

typedef struct block {
    size_t size;
    int free;
    struct block *next;
    struct block *prev;
} block_t;

#define HEADER_SIZE (((sizeof(block_t)) + 15u) & ~((size_t)15u))

static unsigned char *arena = NULL;
static block_t *first = NULL;
static size_t used_bytes = 0;
static size_t used_blocks = 0;

static void ensure_arena(void) {
    void *memory;
    if (arena != NULL) {
        return;
    }
    memory = mmap(NULL, ARENA_BYTES, PROT_READ | PROT_WRITE,
                  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (memory == MAP_FAILED) {
        arena = NULL;
        return;
    }
    arena = (unsigned char *)memory;
    first = (block_t *)arena;
    first->size = ARENA_BYTES - HEADER_SIZE;
    first->free = 1;
    first->next = NULL;
    first->prev = NULL;
}

static void *payload(block_t *block) {
    return (void *)((unsigned char *)block + HEADER_SIZE);
}

static block_t *header(void *pointer) {
    return (block_t *)((unsigned char *)pointer - HEADER_SIZE);
}

static void split(block_t *block, size_t size) {
    block_t *rest;
    if (block->size < size + HEADER_SIZE + 16u) {
        return;
    }
    rest = (block_t *)((unsigned char *)payload(block) + size);
    rest->size = block->size - size - HEADER_SIZE;
    rest->free = 1;
    rest->next = block->next;
    rest->prev = block;
    if (rest->next != NULL) {
        rest->next->prev = rest;
    }
    block->next = rest;
    block->size = size;
}

void *my_malloc(size_t size) {
    block_t *block;
    if (size == 0) {
        return NULL;
    }
    ensure_arena();
    if (first == NULL) {
        return NULL;
    }
    size = (size + 15u) & ~((size_t)15u);
    for (block = first; block != NULL; block = block->next) {
        if (block->free && block->size >= size) {
            split(block, size);
            block->free = 0;
            used_bytes += block->size;
            used_blocks++;
            return payload(block);
        }
    }
    return NULL;
}

void *my_calloc(size_t count, size_t size) {
    size_t total = count * size;
    void *memory = my_malloc(total);
    if (memory != NULL) {
        memset(memory, 0, total);
    }
    return memory;
}

static void coalesce(block_t *block) {
    if (block->next != NULL && block->next->free) {
        block->size += HEADER_SIZE + block->next->size;
        block->next = block->next->next;
        if (block->next != NULL) {
            block->next->prev = block;
        }
    }
    if (block->prev != NULL && block->prev->free) {
        block_t *previous = block->prev;
        previous->size += HEADER_SIZE + block->size;
        previous->next = block->next;
        if (block->next != NULL) {
            block->next->prev = previous;
        }
    }
}

void my_free(void *pointer) {
    block_t *block;
    if (pointer == NULL) {
        return;
    }
    block = header(pointer);
    block->free = 1;
    used_bytes -= block->size;
    used_blocks--;
    coalesce(block);
}

void *my_realloc(void *pointer, size_t size) {
    block_t *block;
    void *replacement;
    if (pointer == NULL) {
        return my_malloc(size);
    }
    if (size == 0) {
        my_free(pointer);
        return NULL;
    }
    block = header(pointer);
    if (block->size >= size) {
        return pointer;
    }
    replacement = my_malloc(size);
    if (replacement == NULL) {
        return NULL;
    }
    memcpy(replacement, pointer, block->size);
    my_free(pointer);
    return replacement;
}

void alloc_stats(size_t *bytes_in_use, size_t *blocks) {
    if (bytes_in_use != NULL) {
        *bytes_in_use = used_bytes;
    }
    if (blocks != NULL) {
        *blocks = used_blocks;
    }
}
