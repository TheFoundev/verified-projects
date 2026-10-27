#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../src/allocator.h"

static void test_malloc_and_free(void) {
    void *pointer = my_malloc(64);
    assert(pointer != NULL);
    memset(pointer, 'x', 64);

    size_t bytes = 0;
    size_t blocks = 0;
    alloc_stats(&bytes, &blocks);
    assert(bytes >= 64);
    assert(blocks >= 1);

    my_free(pointer);
    alloc_stats(&bytes, &blocks);
    assert(blocks == 0);
}

static void test_calloc_is_zeroed(void) {
    unsigned char *pointer = (unsigned char *)my_calloc(16, 4);
    assert(pointer != NULL);
    for (int i = 0; i < 64; i++) {
        assert(pointer[i] == 0);
    }
    my_free(pointer);
}

static void test_realloc_keeps_the_data(void) {
    char *pointer = (char *)my_malloc(16);
    assert(pointer != NULL);
    memcpy(pointer, "hello", 6);

    char *bigger = (char *)my_realloc(pointer, 64);
    assert(bigger != NULL);
    assert(strncmp(bigger, "hello", 6) == 0);
    my_free(bigger);
}

static void test_the_arena_is_used_up(void) {
    // The arena is finite, so asking for a lot has to fail at some point.
    void *pointers[4096];
    int taken = 0;
    for (int i = 0; i < 4096; i++) {
        pointers[i] = my_malloc(64 * 1024);
        if (pointers[i] == NULL) {
            break;
        }
        taken++;
    }
    assert(taken < 4096);
    for (int i = 0; i < taken; i++) {
        my_free(pointers[i]);
    }
}

static void test_free_of_null_is_safe(void) {
    my_free(NULL);
}

int main(void) {
    test_malloc_and_free();
    test_calloc_is_zeroed();
    test_realloc_keeps_the_data();
    test_the_arena_is_used_up();
    test_free_of_null_is_safe();
    printf("all allocator tests passed\n");
    return 0;
}
