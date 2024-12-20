document.addEventListener('DOMContentLoaded', function() {
    let intervalId = null; // Global variable to hold the interval ID
    // Configuration and state
    const config = {
        processActionButtons: {
            'segment-button': 'SEGMENT',
            'make-waypoint-button': 'MAKE_WAYPOINT', 
            'cancel-button': 'CANCEL',
            'accept-button': 'ACCEPT',
            'cycle-left-button': 'CYCLE_LEFT',
            'cycle-right-button': 'CYCLE_RIGHT'
        }
    };

    const state = {
        teleopEnabled: false,
        gazeEnabled: false
    };

    // UI Elements
    const elements = {
        // Video elements
        videoFeed: document.getElementById('video-feed'),
        videoTopicsSelect: document.getElementById('video-topics'),
        
        // Control buttons
        teleopControls: document.querySelector('.teleop-controls'),
        teleopToggle: document.getElementById('teleop-toggle'),
        gazeToggle: document.getElementById('gaze-toggle'),
        makeMissionFilesButton: document.getElementById('make-mission-files'),
        
        // Mission buttons
        missionStart: document.getElementById('mission-start'),
        missionPause: document.getElementById('mission-pause'),
        missionResume: document.getElementById('mission-resume'),
        missionAbort: document.getElementById('mission-abort'),
        endInspection: document.getElementById('end-inspection'),
        
        // State elements
        connectionStatus: document.getElementById('connection-status'),
        missionState: document.getElementById('mission-state')
    };

    const fileTreeHandler = {
        updateFileTree(filesData) {
            const fileTree = document.querySelector('.file-tree');
            if (!filesData || Object.keys(filesData).length === 0) {
                fileTree.innerHTML = `
                    <div class="file-tree-empty">
                        No files loaded. Try clicking the "Make Mission Files" button...
                    </div>
                `;
                return;
            }

            const formatTree = (name, data) => {
                let html = '<ul>';
                if (Array.isArray(data)) {
                    data.forEach(item => {
                        html += `
                            <li>
                                <span class="folder">📁 waypoint_${String(item.index).padStart(2, '0')}</span>
                                <ul>
                        `;
                        
                        // Add image file if it exists
                        if (item.has_image) {
                            html += `
                                <li>
                                    <span class="file">📄 waypoint_image_${String(item.index).padStart(2, '0')}.jpeg</span>
                                </li>
                            `;
                        }
                        
                        // Add waypoint.yaml
                        html += `
                                <li>
                                    <span class="file">📄 waypoint.yaml</span>
                                </li>
                            </ul>
                            </li>
                        `;
                    });
                }
                html += '</ul>';
                return html;
            };

            fileTree.innerHTML = `
                <div class="file-tree-root">
                    <span class="folder">📁 Mission Files</span>
                    ${formatTree('root', filesData)}
                </div>
            `;
        }
    };


    // Video feed handling
    const videoHandler = {
        setup() {
            const timestamp = new Date().getTime();
            elements.videoFeed.src = `/video_feed?t=${timestamp}`;
            
            elements.videoFeed.onerror = () => {
                console.error('Video feed error - attempting to reconnect...');
                setTimeout(() => {
                    if (document.visibilityState === 'visible') {
                        this.setup();
                    }
                }, 1000);
            };
        },

        handleTopicChange(selectedTopic) {
            fetch('/button_press', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `video_topic_selected=${encodeURIComponent(selectedTopic)}`
            }).then(() => this.setup());
        }
    };

    // Teleop handling
    const teleopHandler = {
        toggleTeleop() {
            state.teleopEnabled = !state.teleopEnabled;
            elements.teleopToggle.textContent = state.teleopEnabled ? 'Disable Teleop' : 'Enable Teleop';
            elements.teleopControls.classList.toggle('hidden');
            
            fetch('/button_press', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: 'teleop_toggle_pressed=true'
            });
        },

        

        handleButtonPress(buttonId, isPressed) {
            if (!state.teleopEnabled) return;

            if (isPressed) {
                // Start sending the command continuously every 100ms while the button is pressed
                intervalId = setInterval(() => {
                    fetch('/button_press', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: `teleop_button_pressed=${encodeURIComponent(buttonId)}`
                    });
                }, 100); // Adjust the interval as needed (e.g., 100ms)
            } else {
                // Stop sending the command when the button is released
                clearInterval(intervalId);
                intervalId = null;

                // Optionally, send a "release" message if needed
                fetch('/button_press', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: `teleop_button_release=${encodeURIComponent(buttonId)}`
                });
            }
        }

    };

    // Mission control handling
    const missionHandler = {
        handleButton(action) {
            fetch('/button_press', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `mission_command_button_pressed=${action}`
            });
        }
    };

    const makeMissionFilesHandler = {
        handleButton() {
            fetch('/button_press', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `make_mission_files_button_pressed=true`
            });
        }
    };

    // State handling
    const stateHandler = {
        updateUI(state) {
            if (elements.connectionStatus) {
                elements.connectionStatus.textContent = state.robot_connected ? 'Connected' : 'Disconnected';
            }
            if (elements.missionState) {
                elements.missionState.textContent = state.mission_state || 'Unknown';
            }
            if ('mission_files' in state) {
                        fileTreeHandler.updateFileTree(state.mission_files);
                    }
        },

        async pollState() {
            try {
                const response = await fetch('/state');
                const state = await response.json();
                
                this.updateUI(state);
                this.updateInterfaceBanner(state);
            } catch (error) {
                console.error('Error polling state:', error);
            }
        },

        updateInterfaceBanner(state) {
            const interfaceBanner = document.querySelector('.interface-banner');
            const currentMode = interfaceBanner.dataset.mode;

            if (state.processing_mode && state.processing_mode !== currentMode) {
                this.updateBannerContent(interfaceBanner, state.processing_mode);
                interfaceBanner.dataset.mode = state.processing_mode;
                attachProcessActionButtonListeners();
            }
        },

        updateBannerContent(banner, mode) {
            const content = {
                'INITIAL_FIXATION': `
                    <button id="segment-button">Segment</button>
                    <button id="cancel-button">Cancel</button>
                `,
                'SEGMENTATION_RESULTS': `
                    <button id="cycle-left-button">←</button>
                    <button id="cycle-right-button">→</button>
                    <button id="accept-button">Accept</button>
                    <button id="cancel-button">Cancel</button>
                `,
                'WAYPOINT_RESULTS': `
                    <button id="accept-button">Accept</button>
                    <button id="cancel-button">Cancel</button>
                `
            };
            banner.innerHTML = content[mode] || '';
        }
    };

    // Event listeners setup
    function setupEventListeners() {
        // Video feed listeners
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                videoHandler.setup();
            }
        });
        elements.videoTopicsSelect.addEventListener('change', (e) => videoHandler.handleTopicChange(e.target.value));

        // Teleop listeners
        elements.teleopToggle.addEventListener('click', () => teleopHandler.toggleTeleop());
        document.querySelectorAll('.teleop-btn').forEach(button => {
            button.addEventListener('mousedown', () => teleopHandler.handleButtonPress(button.id, true));
            button.addEventListener('mouseup', () => teleopHandler.handleButtonPress(button.id, false));
            button.addEventListener('mouseleave', () => teleopHandler.handleButtonPress(button.id, false)); // Stop when cursor leaves button
        });

        // Mission button listeners
        elements.missionStart.addEventListener('click', () => missionHandler.handleButton('start'));
        elements.missionPause.addEventListener('click', () => missionHandler.handleButton('pause'));
        elements.missionResume.addEventListener('click', () => missionHandler.handleButton('resume'));
        elements.missionAbort.addEventListener('click', () => missionHandler.handleButton('abort'));
        elements.endInspection.addEventListener('click', () => missionHandler.handleButton('end_inspection'));

        // Make mission files button
        elements.makeMissionFilesButton.addEventListener('click', () => makeMissionFilesHandler.handleButton());

        // Gaze toggle
        elements.gazeToggle.addEventListener('click', function() {
            state.gazeEnabled = !state.gazeEnabled;
            this.textContent = state.gazeEnabled ? 'Disable Gaze' : 'Enable Gaze';
            fetch('/button_press', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: 'gaze_button_pressed=true'
            });
        });
    }

    // Process action button handling
    function attachProcessActionButtonListeners() {
        Object.keys(config.processActionButtons).forEach(buttonId => {
            const button = document.getElementById(buttonId);
            if (button) {
                button.addEventListener('click', () => {
                    fetch('/button_press', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: `process_action_button_pressed=${config.processActionButtons[buttonId]}`
                    });
                });
            }
        });
    }

    // Initialize
    videoHandler.setup();
    setupEventListeners();
    attachProcessActionButtonListeners();
    setInterval(() => stateHandler.pollState(), 1000);
    stateHandler.pollState();
});