idf_component_register(SRCS "oled_ssd1306.c" "smart_monitor_main.c" "sensors.c" "udp_sender.c"
                    PRIV_REQUIRES spi_flash esp_wifi esp_wifi_remote nvs_flash esp_adc driver lwip
                    INCLUDE_DIRS "")
