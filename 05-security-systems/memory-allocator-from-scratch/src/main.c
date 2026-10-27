#include "allocator.h"

#include <stdio.h>

int main(void) {
    int *numbers = (int *)my_malloc(32 * sizeof(int));
    char *text = (char *)my_calloc(16, 1);
    size_t bytes = 0;
    size_t blocks = 0;
    int index;

    if (numbers == NULL || text == NULL) {
        fprintf(stderr, "allocation failed\n");
        return 1;
    }
    for (index = 0; index < 32; index++) {
        numbers[index] = index * index;
    }
    numbers = (int *)my_realloc(numbers, 64 * sizeof(int));
    for (index = 32; index < 64; index++) {
        numbers[index] = index;
    }
    for (index = 0; index < 16; index++) {
        if (text[index] != 0) {
            fprintf(stderr, "calloc did not zero memory\n");
            return 1;
        }
    }

    alloc_stats(&bytes, &blocks);
    printf("allocated %zu bytes in %zu blocks\n", bytes, blocks);
    printf("last value: %d\n", numbers[63]);

    my_free(numbers);
    my_free(text);
    alloc_stats(&bytes, &blocks);
    printf("after free: %zu bytes in %zu blocks\n", bytes, blocks);
    return 0;
}
