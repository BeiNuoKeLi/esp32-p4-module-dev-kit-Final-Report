# ESP32-P4 v1.0 PSRAM Bug Report

## Summary

ESP32-P4 v1.0 (eco2-20240710) with 32MB HEX PSRAM (AP Memory, Waveshare Module-DEV-KIT) exhibits 3 reproducible hardware-level failures under systematic stress testing.

## Environment

| Item | Detail |
|------|--------|
| Chip | ESP32-P4 v1.0 (eco2-20240710) |
| Board | Waveshare ESP32-P4-Module-DEV-KIT |
| PSRAM | 32MB HEX (AP Memory, vendor 0x0D) |
| IDF | v5.5.1 |
| PSRAM Speed | 80MHz |
| PSRAM Mode | HEX (16-line) |
| BSS in PSRAM | Disabled |

## Reproduction

A dedicated test project is available at `psram_test/`. Build and flash:

```bash
cd psram_test
idf.py set-target esp32p4
idf.py build flash monitor
```

## Test Results

### 1. Cross-region memcpy crash (100% reproducible)

**Test**: `memcpy(internal_RAM, PSRAM_buffer, 256KB)`

**Result**: Immediate Guru Meditation — Load access fault in FreeRTOS scheduler
```
MEPC: prvSelectHighestPriorityTaskSMP
MTVAL: 0x11111155
```
PSRAM data (fill pattern 0x11) leaks into FreeRTOS task list, corrupting the scheduler.

### 2. Unaligned read returns stale data (100% reproducible)

**Test**: Read uint32 from odd addresses on PSRAM via `memcpy`

**Result**: Returns data from previous cache line instead of current address
```
Offset 1:   Expected 0x00010001, Got 0x04030201
Offset 129: Expected 0x00810081, Got 0x84838281
```

### 3. Dual-core concurrent PSRAM access deadlock (100% reproducible)

**Test**: Task A writes PSRAM on Core 0, Task B reads PSRAM on Core 1

**Result**: `psram_wr` task freezes, task_wdt triggers:
```
task_wdt: CPU 0: psram_wr (stuck)
MEPC: esp_crosscore_int_send (deadlock in cross-core interrupt)
```

## Additional Observations

- PSRAM-only internal operations (allocate, write, read all within PSRAM) work correctly
- malloc/free stress passes 200 iterations
- The PSRAM SPI memory test passes on every boot
- Issue persists at all speeds (20/80/200MHz)
- Similar board (ESPHome #16903) with same chip revision reports PSRAM crashes

## Submission Targets

- **GitHub**: https://github.com/espressif/esp-idf/issues (new issue with this content)
- **ESP32 Forum**: https://esp32.com (cross-post)
