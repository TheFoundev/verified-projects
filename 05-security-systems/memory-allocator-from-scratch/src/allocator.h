#ifndef ALLOCATOR_H
#define ALLOCATOR_H

#include <stddef.h>

void *my_malloc(size_t size);
void *my_calloc(size_t count, size_t size);
void *my_realloc(void *pointer, size_t size);
void my_free(void *pointer);
void alloc_stats(size_t *bytes_in_use, size_t *blocks);

#endif
