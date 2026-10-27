#ifndef KERNEL_H
#define KERNEL_H

#include <stddef.h>
#include <stdint.h>

size_t kstrlen(const char *text);
int kstrcmp(const char *left, const char *right);
void *kmemset(void *destination, int value, size_t count);
void *kmemcpy(void *destination, const void *source, size_t count);
uint8_t vga_color(uint8_t foreground, uint8_t background);
uint16_t vga_entry(char character, uint8_t color);
size_t vga_index(size_t row, size_t column);
void kernel_main(uint32_t magic, uint32_t address);

#endif
