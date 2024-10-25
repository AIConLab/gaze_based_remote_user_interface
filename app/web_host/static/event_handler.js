
document.addEventListener('DOMContentLoaded', function() {
    // Process Action Buttons
    const processActionButtons = {
        'segment-button': 'SEGMENT',
        'make-waypoint-button': 'MAKE_WAYPOINT', 
        'cancel-button': 'CANCEL',
        'accept-button': 'ACCEPT',
        'cycle-left-button': 'CYCLE_LEFT',
        'cycle-right-button': 'CYCLE_RIGHT'
    };


    // Video feed elements
    const videoTopicsSelect = document.getElementById('video-topics');
    const videoFeed = document.getElementById('video-feed');

    // Gaze button
    const gazeToggle = document.getElementById('gaze-toggle');

    // Mission buttons
    const missionStart = document.getElementById('mission-start');
    const missionPause = document.getElementById('mission-pause');
    const missionStop = document.getElementById('mission-stop');
    

    // Setup video feed
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

    
    // Handle gaze toggle
    gazeToggle.addEventListener('click', function() {
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: 'gaze_button_pressed=true'
        });
    });

    // Handle mission buttons
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


    // Add event listeners for all process action buttons
    Object.keys(processActionButtons).forEach(buttonId => {
        const button = document.getElementById(buttonId);
        if (button) {
            button.addEventListener('click', function() {
                fetch('/button_press', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: `process_action_button_press=${processActionButtons[buttonId]}`
                });
            });
        }
    });
});