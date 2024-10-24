# gaze_based_remote_user_interface

Application used for demoing gaze based robot control interface


## Setup App Images

1. `cd .docker`
2. `docker compose build`

### Configuration

If a `pupil_capture_settings` file is not available in the `app/pupil_module` directory, or if a recalibration is needed, then follow the steps outlines in the following repo to generate the files: [pupil_capture_docker](https://github.com/AIConLab/pupil_capture_docker)

## Usage

1. `cd .docker`
2. `docker compose up`
3. Open browser and navigate to `http://0.0.0.0:5000/`

