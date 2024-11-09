# Gaze-Based Remote Robot Control Interface

A web-based application for remote robot control using gaze-based interaction for autonomous inspection missions.

## System Overview

This application enables operators to:
- Upload and process mission files for autonomous robot navigation
- Monitor robot status and mission progress
- Control robot movement using teleop controls when needed
- Use gaze-based interaction for precise target identification
- Select and verify segmentation masks for inspection targets
- Manage the complete inspection mission lifecycle

## Setup

1. Ensure your system meets these prerequisites:
   - Docker installed
   - Network connection to the robot
   - Pupil Labs eye tracking hardware properly calibrated

2. Configure the robot network:
   ```
   cd .docker
   ```
   Create a `.env` file with:
   ```
   ROBOT_IP=robot_ip
   ROBOT_HOSTNAME=robot_hostname
   ```

3. Build and start the application:
   ```
   cd .docker
   docker compose build
   docker compose up
   ```

4. Open your web browser and navigate to:
   ```
   http://0.0.0.0:5000/
   ```

## Operator Instructions

### Mission Setup Phase

1. **Prepare Mission Files**
   - Place the mission KMZ file as `mission.kmz` in the `/app_shared_data/mission_files/files_to_process` directory
   - Create subdirectories for each waypoint: `waypoint_0`, `waypoint_1`, etc.
   - Place corresponding JPEG images in each waypoint directory

You can now start the application and proceed with the mission setup.

2. **Process Mission Files**
   - Click the "Make Mission Files" button in the interface
   - The white box below will show the processed waypoints
   - Verify that all waypoints and images are correctly loaded

### Mission Execution

1. **Start Mission**
   - Ensure robot shows "Connected" status
   - Click "Mission Start" to begin autonomous navigation
   - Monitor mission state updates in the interface

2. **At Each Waypoint**
   - Robot will automatically navigate to waypoints in sequence
   - When reaching a waypoint, robot enters inspection state
   - Robot should automatically face the inspection target

3. **Target Identification**
   - If robot positioning needs adjustment, use the teleop control pad
   - Click "Enable Gaze" to activate gaze-based interaction
   - Look at the inspection target to establish fixation
   - Green dots show the latest gaze locations, a red dot will appear showing your fixation point

4. **Target Segmentation**
   - When fixation is correct, click "Segment"
   - System will process and show three potential segmentation masks
   - Use left/right arrows to cycle through masks if needed
   - Click "Accept" when the correct mask is displayed
   - Click "Cancel" to retry segmentation if needed

5. **Inspection Process**
   - After mask selection, data is sent to robot
   - Robot performs detailed inspection of the selected area
   - Upon completion, robot proceeds to next waypoint
   - Process repeats until all waypoints are inspected

### Controls Reference

- **Mission Control Buttons**
  - Start: Begin mission execution
  - Pause: Temporarily halt mission
  - Resume: Continue paused mission
  - Abort: Emergency stop and end mission

- **Teleop Controls**
  - Directional pad for manual robot positioning
  - Click "Disable Teleop" to hide controls

- **Process Control Buttons**
  - Enable/Disable Gaze: Toggle gaze tracking
  - Segment: Process fixation point
  - Make Waypoint: Create new waypoint (if enabled)
  - Cancel: Abort current processing

### Status Monitoring

- **Robot Connection Status**: Shows current connection state
- **Mission State**: Displays current mission phase
- **Video Feed**: Shows robot's camera view
- **Mission Files**: Lists processed waypoints and status

### Troubleshooting

1. If robot shows "Disconnected":
   - Check network configuration
   - Verify robot IP and hostname
   - Ensure robot is powered on and network accessible

2. If gaze tracking is inaccurate:
   - Recalibrate Pupil Labs hardware
   - Check lighting conditions
   - Ensure proper distance from screen

3. If segmentation fails:
   - Try adjusting fixation point
   - Ensure clear view of target
   - Check for adequate lighting and contrast

4. If mission doesn't start:
   - Verify mission files are properly processed
   - Check robot state is ready for mission
   - Ensure no existing mission is in progress

## Support

For additional troubleshooting or support:
- Check the application logs in the `logs` directory
- Refer to the [pupil_capture_docker](https://github.com/AIConLab/pupil_capture_docker) repository for eye tracking setup