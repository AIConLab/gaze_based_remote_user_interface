from flask import Flask, render_template, Response, request, redirect, url_for
import cv2
import os

app = Flask(__name__, template_folder=os.path.abspath('templates'))

# Placeholder data - in a real application, you'd use a database or other data store
robot_status = "Disconnected"
mission_status = "No active mission"
current_display = "Default"
display_options = ["Default", "Gaze Overlay", "Object Detection", "Map View"]

def gen_frames():
    camera = cv2.VideoCapture(0)
    while True:
        success, frame = camera.read()
        if not success:
            break
        else:
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/', methods=['GET', 'POST'])
def index():
    global robot_status, mission_status, current_display
    
    if request.method == 'POST':
        if 'connect_robot' in request.form:
            robot_status = "Connected"
        elif 'disconnect_robot' in request.form:
            robot_status = "Disconnected"
        elif 'start_mission' in request.form:
            mission_status = "Mission Active"
        elif 'end_mission' in request.form:
            mission_status = "No active mission"
        elif 'display_option' in request.form:
            current_display = request.form['display_option']
    
    return render_template('index.html', 
                           robot_status=robot_status,
                           mission_status=mission_status,
                           current_display=current_display,
                           display_options=display_options)

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)