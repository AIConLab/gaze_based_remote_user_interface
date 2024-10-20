document.addEventListener('DOMContentLoaded', function() {
    const videoTopicsSelect = document.getElementById('video-topics');
    const gazeToggle = document.getElementById('gaze-toggle');
    const missionStart = document.getElementById('mission-start');
    const missionPause = document.getElementById('mission-pause');
    const missionStop = document.getElementById('mission-stop');

    videoTopicsSelect.addEventListener('change', function() {
        const selectedTopic = this.value;
        if (selectedTopic) {
            fetch('/button_press', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: `video_topic_selected=${encodeURIComponent(selectedTopic)}`
            });
        }
    });

    gazeToggle.addEventListener('click', function() {
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: 'gaze_button_pressed=true'
        });
    });

    missionStart.addEventListener('click', function() {
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: 'mission_start_button_pressed=true'
        });
    });

    missionPause.addEventListener('click', function() {
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: 'mission_pause_button_pressed=true'
        });
    });

    missionStop.addEventListener('click', function() {
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: 'mission_stop_button_pressed=true'
        });
    });
});
