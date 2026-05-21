idf_component_register(SRCS "hello_world_main.c" "sensors.c"
                    PRIV_REQUIRES spi_flash esp_wifi esp_wifi_remote nvs_flash esp_adc driver
                    INCLUDE_DIRS "")
