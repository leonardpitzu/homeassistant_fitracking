"""GraphQL documents for Fi's API.

Field names mirror Fi's schema exactly; anything renamed belongs in models.py.
Deliberately narrower than pytryfi's equivalents: the connection-state fragment
does not pull UserDetails, so account email no longer rides along with every
device poll.
"""

from __future__ import annotations

PET_MODE_NORMAL = "NORMAL"
PET_MODE_LOST = "LOST_DOG"
ACTIVITY_ONGOING_WALK = "OngoingWalk"

_LED_FRAGMENT = """
fragment LedColorDetails on LedColor {
  ledColorCode
  hexCode
  name
}
"""

_DEVICE_FRAGMENT = """
fragment DeviceDetails on Device {
  __typename
  id
  moduleId
  info
  hasActiveSubscription
  operationParams {
    mode
    ledEnabled
    ledOffAt
  }
  lastConnectionState {
    __typename
    date
  }
  ledColor { ...LedColorDetails }
  availableLedColors { ...LedColorDetails }
}
"""

_PET_FRAGMENT = """
fragment PetProfile on Pet {
  __typename
  id
  name
  breed { name }
  photos { first { image { fullSize } } }
  device { ...DeviceDetails }
}
"""

# A document carrying a fragment it never uses is a validation error, so the
# mutations get the device fragments only.
_DEVICE_FRAGMENTS = _LED_FRAGMENT + _DEVICE_FRAGMENT
_PET_FRAGMENTS = _DEVICE_FRAGMENTS + _PET_FRAGMENT

# One document per refresh: pets and bases arrive together.
HOUSEHOLDS = (
    """
query {
  currentUser {
    id
    userHouseholds {
      household {
        pets { ...PetProfile }
        bases {
          __typename
          baseId
          name
          online
        }
      }
    }
  }
}
"""
    + _PET_FRAGMENTS
)

# Everything per-pet in a single round trip. GraphQL reports per-field errors
# alongside partial data, so one failing section cannot blank the others.
#
# Fi keys a night by the evening it BEGAN and answers Unavailable until that
# night ends, so last night lives under yesterday's date from the moment it
# settles -- and under the day before that between local midnight and the
# moment the current night settles. Both are asked for in the one round trip.
PET_DETAIL = """
query PetDetail($petId: ID!, $lastNight: DateTime!, $priorNight: DateTime!) {
  pet(id: $petId) {
    ongoingActivity {
      __typename
      start
      areaName
      lastReportTimestamp
      ... on OngoingWalk {
        positions { position { latitude longitude } }
      }
      ... on OngoingRest {
        position { latitude longitude }
        place { name address }
      }
    }
    dailyActivity: currentActivitySummary(period: DAILY) { ...ActivitySummaryDetails }
    weeklyActivity: currentActivitySummary(period: WEEKLY) { ...ActivitySummaryDetails }
    monthlyActivity: currentActivitySummary(period: MONTHLY) { ...ActivitySummaryDetails }
    dailyRest: restSummaryFeed(cursor: null, period: DAILY, limit: 1) { ...RestFeedDetails }
    weeklyRest: restSummaryFeed(cursor: null, period: WEEKLY, limit: 1) { ...RestFeedDetails }
    monthlyRest: restSummaryFeed(cursor: null, period: MONTHLY, limit: 1) { ...RestFeedDetails }
    lastNight: overnightRestSummary(date: $lastNight) { ...OvernightDetails }
    priorNight: overnightRestSummary(date: $priorNight) { ...OvernightDetails }
  }
  getPetHealthTrendsForPet(petId: $petId, period: DAY) {
    behaviorTrends {
      id
      chart {
        ... on PetHealthTrendSegmentedTimeline {
          intervals { intervalType offset }
        }
      }
    }
  }
}

fragment OvernightDetails on OvernightRestSummary {
  __typename
  ... on ConcreteOvernightRestSummary {
    date
    sleepStart
    sleepEnd
    sleepSeconds
  }
}

fragment ActivitySummaryDetails on ActivitySummary {
  start
  end
  totalSteps
  stepGoal
  totalDistance
}

fragment RestFeedDetails on RestSummaryFeed {
  restSummaries {
    start
    end
    data {
      ... on ConcreteRestSummaryData {
        sleepAmounts { type duration }
      }
    }
  }
}
"""

SET_DEVICE_OPS = (
    """
mutation UpdateDeviceOperationParams($input: UpdateDeviceOperationParamsInput!) {
  updateDeviceOperationParams(input: $input) { ...DeviceDetails }
}
"""
    + _DEVICE_FRAGMENTS
)

SET_LED_COLOR = (
    """
mutation SetDeviceLed($moduleId: String!, $ledColorCode: Int!) {
  setDeviceLed(moduleId: $moduleId, ledColorCode: $ledColorCode) { ...DeviceDetails }
}
"""
    + _DEVICE_FRAGMENTS
)
