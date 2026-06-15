idf_component_register(SRCS "sim_poll.c" "oled_ssd1306.c" "smart_monitor_main.c" "sensors.c" "udp_sender.c" "camera_http_fetch.c"
                    PRIV_REQUIRES spi_flash esp_wifi esp_wifi_remote nvs_flash esp_adc driver lwip esp_http_client
                    INCLUDE_DIRS "")
