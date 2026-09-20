DOMAIN = "fitracking"
PLATFORMS = ["device_tracker", "light", "sensor", "select", "binary_sensor"]
DEFAULT_POLLING_RATE = "10"
CONF_POLLING_RATE = "polling"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
SENSOR_STATS_BY_TIME = ["DAILY", "WEEKLY", "MONTHLY"]
SENSOR_STATS_BY_TYPE = ["STEPS", "DISTANCE", "SLEEP", "NAP", "GOAL", "ACTIVE"]

# Keyed by Fi's own behaviour id; "cleaning_self" is what it calls Licking.
BEHAVIOR_META = {
    "barking": {"name": "Barking", "icon": "mdi:bullhorn"},
    "eating": {"name": "Eating", "icon": "mdi:food-drumstick"},
    "drinking": {"name": "Drinking", "icon": "mdi:cup-water"},
    "cleaning_self": {"name": "Licking", "icon": "mdi:emoticon-tongue-outline"},
    "scratching": {"name": "Scratching", "icon": "mdi:hand-back-right"},
}

MANUFACTURER = "Fi"
