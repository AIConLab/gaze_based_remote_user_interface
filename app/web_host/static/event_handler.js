document.addEventListener('DOMContentLoaded', function() {
    const videoTopicsSelect = document.getElementById('video-topics');
    const gazeToggle = document.getElementById('gaze-toggle');
    const missionStart = document.getElementById('mission-start');
    const missionPause = document.getElementById('mission-pause');
    const missionStop = document.getElementById('mission-stop');
    const videoFeed = document.getElementById('video-feed');

    // Set up video feed
    function setupVideoFeed() {
        videoFeed.src = '/video_feed?' + new Date().getTime();
        videoFeed.onload = function() {
            console.log('Video feed loaded successfully');
        };
        videoFeed.onerror = function() {
            console.error('Error loading video feed');
            setTimeout(setupVideoFeed, 5000);
        };
    }

    // Initial setup
    setupVideoFeed();
    updateVideoTopic('');  // Send default topic on page load

    videoTopicsSelect.addEventListener('change', function() {
        const selectedTopic = this.value;
        updateVideoTopic(selectedTopic);
    });

    function updateVideoTopic(topic) {
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: `video_topic_selected=${encodeURIComponent(topic)}`
        }).then(() => {
            setupVideoFeed(); // Reload video feed when topic changes
        });
    }
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