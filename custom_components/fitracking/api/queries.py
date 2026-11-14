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
  operationParams {
    mode
    ledEnabled
    ledOffAt
  }
  lastConnectionState {
    __typename
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
# Rest comes from restFeed, not restSummaryFeed: restSummaryFeed files a whole
# session under the local day it BEGAN, so its daily bucket reads SLEEP 0 for as
# long as the current night started yesterday. restFeed's totals are clipped at
# the period boundary, which is what the Fi app shows for the day, week and month.
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
    dailyRest: restFeed(cursor: null, period: DAY) { ...RestFeedDetails }
    weeklyRest: restFeed(cursor: null, period: WEEK) { ...RestFeedDetails }
    monthlyRest: restFeed(cursor: null, period: MONTH) { ...RestFeedDetails }
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
  restTimeline: getPetBehaviorDetail(
    input: {petId: $petId, behaviorId: "rest", period: DAY}
  ) { ...DayTimelineDetails }
  activityTimeline: getPetBehaviorDetail(
    input: {petId: $petId, behaviorId: "activity", period: DAY}
  ) { ...DayTimelineDetails }
}

# The two bars the Fi app draws on the health page, placed against the local
# day: offset and length are seconds from local midnight. Fi pads any event
# shorter than its minimum render width, so lengths are positions, not totals.
# behaviorId accepts only rest, activity, steps and the five behaviours already
# reported -- "sleep" and "nap" are rejected, so the day/night split has to come
# from overnightRestSummary rather than from a second timeline.
fragment DayTimelineDetails on PetBehaviorDetail {
  ... on PetBehaviorDetailDay {
    segmentedTimeline {
      intervals { intervalType offset length }
    }
  }
}

fragment OvernightDetails on OvernightRestSummary {
  __typename
  ... on ConcreteOvernightRestSummary {
    sleepStart
    sleepEnd
    sleepSeconds
  }
}

fragment ActivitySummaryDetails on ActivitySummary {
  totalSteps
  stepGoal
  totalDistance
  totalActiveTimeSeconds
}

fragment RestFeedDetails on RestFeed {
  restSummary {
    sleepSecondsTotal
    napSecondsTotal
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
