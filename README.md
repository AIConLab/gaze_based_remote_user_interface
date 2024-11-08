# gaze_based_remote_user_interface

Application used for demoing gaze based robot control interface


## Setup App Images

1. `cd .docker`
2. `docker compose build`

### Robot Network Configuration

To ensure the container can connect to your robot, create a `.env` file in the root directory of this project with the following content:
```
ROBOT_IP=robot_ip
ROBOT_HOSTNAME=robot_hostname
```

The resulting directory structure should look like this:
```
.
├── app
├── .docker
├── .env
├── .git
├── .gitignore
└── README.md
```

If you have issues conecting to the robot, the following gist has some useful troubleshooting steps: [Simple script to setup your machine env to use a remote ROS master ](https://gist.github.com/chfritz/8c2adab45a94e091be77c55b0432ad2e?permalink_comment_id=3519694#gistcomment-3519694)


### Pupil Capture Settings

If a `pupil_capture_settings` file is not available in the `app/pupil_module` directory, or if a recalibration is needed, then follow the steps outlines in the following repo to generate the files: [pupil_capture_docker](https://github.com/AIConLab/pupil_capture_docker)



## Usage

1. `cd .docker`
2. `docker compose up`
3. Open browser and navigate to `http://0.0.0.0:5000/`