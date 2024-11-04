document.addEventListener('DOMContentLoaded', function() {

    const teleopControls = document.querySelector('.teleop-controls');
    const teleopToggle = document.getElementById('teleop-toggle');
    let teleopEnabled = false;

    // Handle teleop toggle
    teleopToggle.addEventListener('click', function() {
        teleopEnabled = !teleopEnabled;
        teleopToggle.textContent = teleopEnabled ? 'Disable Teleop' : 'Enable Teleop';
        teleopControls.classList.toggle('hidden');
        
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: 'teleop_toggle_pressed=true'
        });
    });

    // Handle teleop button presses
    document.querySelectorAll('.teleop-btn').forEach(button => {
        button.addEventListener('click', function() {
            if (!teleopEnabled) return;
            fetch('/button_press', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: `teleop_button_pressed=${encodeURIComponent(this.id)}`
            });
        });
    });


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
    let gazeEnabled = false;

    // Mission buttons
    const missionStart = document.getElementById('mission-start');
    const missionPause = document.getElementById('mission-pause');
    const missionResume = document.getElementById('mission-resume');
    const missionAbort = document.getElementById('mission-abort');

    // State elements
    const connectionStatus = document.getElementById('connection-status');
    const missionState = document.getElementById('mission-state');

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
        gazeEnabled = !gazeEnabled;        
        gazeToggle.textContent = gazeEnabled ? 'Disable Gaze' : 'Enable Gaze';
        
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

    missionResume.addEventListener('click', function() {
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: 'mission_resume_button_pressed=true'
        });
    });

    missionAbort.addEventListener('click', function() {
        fetch('/button_press', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: 'mission_abort_button_pressed=true'
        });
    });

    // Function to update UI elements based on state
    function updateUIFromState(state) {
        if (connectionStatus) {
            connectionStatus.textContent = state.robot_connected ? 'Connected' : 'Disconnected';
        }
        
        if (missionState) {
            missionState.textContent = state.mission_state || 'Unknown';
        }
    }

    // Add state polling function
    async function pollState() {
        try {
            const response = await fetch('/state');
            const state = await response.json();
            
            // Update UI elements
            updateUIFromState(state);
            
            const interfaceBanner = document.querySelector('.interface-banner');
            const currentMode = interfaceBanner.dataset.mode;

            if (state.processing_mode && state.processing_mode !== currentMode) {
                // Update the interface banner based on the new mode
                switch(state.processing_mode) {
                    case 'INITIAL_FIXATION':
                        interfaceBanner.innerHTML = `
                            <button id="segment-button">Segment</button>
                            <button id="make-waypoint-button">Make Waypoint</button>
                            <button id="cancel-button">Cancel</button>
                        `;
                        break;
                    case 'SEGMENTATION_RESULTS':
                        interfaceBanner.innerHTML = `
                            <button id="cycle-left-button">←</button>
                            <button id="cycle-right-button">→</button>
                            <button id="accept-button">Accept</button>
                            <button id="cancel-button">Cancel</button>
                        `;
                        break;
                    case 'WAYPOINT_RESULTS':
                        interfaceBanner.innerHTML = `
                            <button id="accept-button">Accept</button>
                            <button id="cancel-button">Cancel</button>
                        `;
                        break;
                    default:
                        interfaceBanner.innerHTML = '';
                }
                
                // Update the data-mode attribute
                interfaceBanner.dataset.mode = state.processing_mode;
                
                // Reattach event listeners to new buttons
                attachProcessActionButtonListeners();
            }
        } catch (error) {
            console.error('Error polling state:', error);
        }
    }

    // Function to attach event listeners to process action buttons
    function attachProcessActionButtonListeners() {
        Object.keys(processActionButtons).forEach(buttonId => {
            const button = document.getElementById(buttonId);
            if (button) {
                button.addEventListener('click', function() {
                    fetch('/button_press', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                        },
                        body: `process_action_button_pressed=${processActionButtons[buttonId]}`
                    });
                });
            }
        });
    }

    // Poll every second
    setInterval(pollState, 1000);

    // Initial state poll
    pollState();

    // Add event listeners for all process action buttons
    attachProcessActionButtonListeners();
});