document.addEventListener('DOMContentLoaded', function() {
    const videoTopicsSelect = document.getElementById('video-topics');
    const gazeToggle = document.getElementById('gaze-toggle');
    const missionStart = document.getElementById('mission-start');
    const missionPause = document.getElementById('mission-pause');
    const missionStop = document.getElementById('mission-stop');
    const videoFeed = document.getElementById('video-feed');
    
    function setupVideoFeed() {
        // Add timestamp to prevent caching
        const timestamp = new Date().getTime();
        videoFeed.src = `/video_feed?t=${timestamp}`;
        
        videoFeed.onerror = function() {
            console.error('Video feed error - attempting to reconnect...');
            setTimeout(() => {
                if (document.visibilityState === 'visible') {
                    setupVideoFeed();
                }
            }, 1000);
        };
    }

    // Monitor visibility changes
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible') {
            setupVideoFeed();
        }
    });

    // Initial setup
    setupVideoFeed();
    
    // Handle topic changes
    videoTopicsSelect.addEventListener('change', function() {
        const selectedTopic = this.value;
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: `video_topic_selected=${encodeURIComponent(selectedTopic)}`
        }).then(() => {
            setupVideoFeed();
        });
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