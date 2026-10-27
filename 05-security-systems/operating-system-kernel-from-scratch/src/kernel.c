#include "kernel.h"

#define VGA_WIDTH 80
#define VGA_HEIGHT 25
#define VGA_MEMORY ((volatile uint16_t *)0xB8000)

size_t kstrlen(const char *text) {
    size_t length = 0;
    while (text[length] != '\0') {
        length++;
    }
    return length;
}

int kstrcmp(const char *left, const char *right) {
    size_t index = 0;
    while (left[index] != '\0' && left[index] == right[index]) {
        index++;
    }
    return (unsigned char)left[index] - (unsigned char)right[index];
}

void *kmemset(void *destination, int value, size_t count) {
    unsigned char *bytes = (unsigned char *)destination;
    size_t index;
    for (index = 0; index < count; index++) {
        bytes[index] = (unsigned char)value;
    }
    return destination;
}

void *kmemcpy(void *destination, const void *source, size_t count) {
    unsigned char *target = (unsigned char *)destination;
    const unsigned char *origin = (const unsigned char *)source;
    size_t index;
    for (index = 0; index < count; index++) {
        target[index] = origin[index];
    }
    return destination;
}

uint8_t vga_color(uint8_t foreground, uint8_t background) {
    return (uint8_t)(foreground | (background << 4));
}

uint16_t vga_entry(char character, uint8_t color) {
    return (uint16_t)((uint8_t)character | ((uint16_t)color << 8));
}

size_t vga_index(size_t row, size_t column) {
    return row * (size_t)VGA_WIDTH + column;
}

void kernel_main(uint32_t magic, uint32_t address) {
    const char *message = "foundev kernel";
    size_t index = 0;
    uint8_t color = vga_color(7, 0);
    size_t cell = vga_index(0, 0);
    (void)magic;
    (void)address;

    for (index = 0; index < (size_t)VGA_WIDTH * (size_t)VGA_HEIGHT; index++) {
        VGA_MEMORY[index] = vga_entry(' ', vga_color(7, 0));
    }
    for (index = 0; message[index] != '\0' && index < (size_t)VGA_WIDTH; index++) {
        VGA_MEMORY[cell + index] = vga_entry(message[index], color);
    }
    for (;;) {
        __asm__ volatile("hlt");
    }
}
