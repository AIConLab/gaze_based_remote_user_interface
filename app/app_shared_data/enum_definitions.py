from enum import Enum, unique

@unique
class MissionStates(Enum):
    IDLE = 0
    LOADING_WAYPOINTS = 1
    MISSION_COMPLETED = 2
    NAVIGATING_TO_WAYPOINT = 3
    WAYPOINT_REACHED = 4
    SCANNING_SCENE = 5
    SCANNED_SCENE = 6
    APPROACHING_TARGET = 7
    AWAITING_HUMAN_ASSISTANCE = 8
    INSPECTING_TARGET = 9
    INSPECTION_COMPLETED = 10
    PAUSED = 11
    ABORTED = 12


@unique
class MissionCommandSignals(Enum):
    MISSION_START = 0
    MISSION_PAUSE = 1
    MISSION_ABORT = 2
    MISSION_RESUME = 3

@unique
class ProcessingModes(Enum):
    VIDEO_RENDERING = 0 # Video rendering mode, blank banner
    INITIAL_FIXATION = 1 # Initial fixation mode, banner buttons: "Make Waypoint", "Segment", "Cancel"
    PROCESSING_DATA = 2 # Processing data mode, blank banner
    SEGMENTATION_RESULTS = 3 # Segmentation results mode, banner buttons: "Accept", "Cancel" "Cycle left", "Cycle right"
    WAYPOINT_RESULTS = 4 # Waypoint results mode, banner buttons: "Accept", "Cancel"

@unique
class ProcessingModeActions(Enum):
    MAKE_WAYPOINT = 0
    SEGMENT = 1
    CANCEL = 2
    ACCEPT = 3
    CYCLE_LEFT = 4
    CYCLE_RIGHT = 5