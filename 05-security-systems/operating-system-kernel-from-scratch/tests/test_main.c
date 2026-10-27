#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../src/kernel.h"

static void test_kstrlen(void) {
    assert(kstrlen("hello") == 5);
    assert(kstrlen("") == 0);
}

static void test_kstrcmp(void) {
    assert(kstrcmp("hello", "hello") == 0);
    assert(kstrcmp("hello", "hell") > 0);
    assert(kstrcmp("hell", "hello") < 0);
}

static void test_the_memory_helpers(void) {
    char buffer[16];
    kmemset(buffer, 'a', sizeof(buffer));
    assert(buffer[0] == 'a');
    assert(buffer[15] == 'a');

    char other[16];
    kmemcpy(other, buffer, sizeof(other));
    assert(other[7] == 'a');
}

static void test_the_vga_helpers(void) {
    uint8_t colour = vga_color(0x0f, 0x00);
    assert(colour == 0x0f);
    uint16_t entry = vga_entry('A', 0x07);
    assert((entry & 0xff) == 'A');
    assert((entry >> 8) == 0x07);
    assert(vga_index(1, 2) == 1 * 80 + 2);
}

int main(void) {
    test_kstrlen();
    test_kstrcmp();
    test_the_memory_helpers();
    test_the_vga_helpers();
    printf("all kernel tests passed\n");
    return 0;
}
