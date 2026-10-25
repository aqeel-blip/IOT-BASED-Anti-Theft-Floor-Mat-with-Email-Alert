import os
import json
import cv2
import time
import random
import threading
import numpy as np
import face_recognition
import yagmail
import serial
import serial.tools.list_ports

from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, Response, jsonify,
)
from werkzeug.utils import secure_filename

# ============================================================
# FLASK APP SETUP
# ============================================================

app = Flask(__name__)
app.secret_key = "home-security-secret-key-2024"

# ============================================================
# FOLDERS
# ============================================================

MEMBERS_FOLDER  = "static/uploads/members"
ALERTS_FOLDER   = "static/uploads/alerts"
UNKNOWNS_FOLDER = "static/uploads/unknowns"

for f in [MEMBERS_FOLDER, ALERTS_FOLDER, UNKNOWNS_FOLDER]:
    os.makedirs(f, exist_ok=True)

# ============================================================
# SETTINGS
# ============================================================

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
FACE_MATCH_THRESHOLD     = 0.50

# --- Email settings (use Gmail App Password) ---
EMAIL_USER         = os.getenv("EMAIL_USER",         "batterycallout782@gmail.com")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "YOUR_GMAIL_APP_PASSWORD")
ALERT_EMAIL        = os.getenv("ALERT_EMAIL",        "aqeelkhankhattak66@gmail.com")

# Arduino baud rate (must match Arduino sketch)
ARDUINO_BAUD = 9600

# ============================================================
# GLOBAL STATE
# ============================================================

live_camera_running  = False
capture_cam_running  = False
capture_latest_frame = None

# What Arduino last told us (voltage sensor triggered side)
# "none" / "right" / "left"
arduino_trigger = "none"

# What Flask must send back to Arduino
# 0 = no face yet   1 = unknown detected   2 = known detected
arduino_reply = 0

# Serial connection object (shared)
arduino_serial = None
arduino_lock   = threading.Lock()

# Detection state for frontend polling
detection_state = {
    "screen_red":    False,
    "popup":         False,
    "person_name":   "",
    "person_image":  "",
    "alert_time":    "",
    "arduino_side":  "none",   # "right" or "left"
}

# ============================================================
# DATABASE
# ============================================================

DB_FILE = "database.json"

def load_db():
    if not os.path.exists(DB_FILE):
        return {"members": {}, "alerts": {}}
    with open(DB_FILE, "r") as fh:
        return json.load(fh)

def save_db(data):
    with open(DB_FILE, "w") as fh:
        json.dump(data, fh, indent=2)

# ============================================================
# HELPERS
# ============================================================

def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_uploaded_file(file, folder, prefix):
    fname  = secure_filename(file.filename)
    unique = f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}_{fname}"
    path   = os.path.join(folder, unique).replace("\\", "/")
    file.save(path)
    return path


def save_frame_to_disk(frame, folder, prefix):
    fname = f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}.jpg"
    path  = os.path.join(folder, fname).replace("\\", "/")
    cv2.imwrite(path, frame)
    return path


def open_camera(index=0):
    backends = []
    if hasattr(cv2, "CAP_DSHOW"): backends.append(cv2.CAP_DSHOW)
    if hasattr(cv2, "CAP_MSMF"):  backends.append(cv2.CAP_MSMF)
    backends.append(None)
    for backend in backends:
        cap = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            for _ in range(5): cap.read()
            return cap
        cap.release()
    return None

# ============================================================
# SERIAL / ARDUINO COMMUNICATION
# ============================================================

# We no longer need a fixed ARDUINO_PORT — auto-detect handles everything
ARDUINO_BAUD = 9600

# Track which port we found Arduino on (shown in UI)
arduino_found_port = "Not connected"


def _try_port(device):
    """
    Open one serial port, wait for Arduino reset, then read up to 10 lines
    looking for the handshake string HOME_SECURITY_READY.
    Returns open Serial object if found, else None.
    """
    try:
        s = serial.Serial(device, ARDUINO_BAUD, timeout=2)
        time.sleep(2.2)          # wait for Arduino bootloader reset
        for _ in range(10):
            raw = s.readline()
            line = raw.decode("utf-8", errors="ignore").strip()
            if line:
                print(f"  [{device}] received: {line!r}")
            if "HOME_SECURITY_READY" in line:
                return s          # correct Arduino found
        s.close()
    except Exception as e:
        print(f"  [{device}] error: {e}")
    return None


def connect_arduino():
    """
    Scan every available serial port.
    Connect to the one that replies HOME_SECURITY_READY within 2 seconds.
    If nothing found, run without Arduino (system still works via camera).
    """
    global arduino_serial, arduino_found_port

    ports = serial.tools.list_ports.comports()

    if not ports:
        print("No serial ports found. Running without Arduino.")
        arduino_serial    = None
        arduino_found_port = "No ports found"
        return False

    print(f"Scanning {len(ports)} port(s) for Arduino…")

    for p in ports:
        print(f"Trying {p.device} ({p.description})…")
        conn = _try_port(p.device)
        if conn:
            arduino_serial     = conn
            arduino_found_port = p.device
            print(f"✅ Arduino found on {p.device}")
            return True

    print("Arduino not found on any port. Running without Arduino.")
    arduino_serial     = None
    arduino_found_port = "Not found"
    return False


def reconnect_arduino():
    """
    Called from /arduino_reconnect route.
    Closes existing connection and re-scans all ports.
    """
    global arduino_serial
    if arduino_serial and arduino_serial.is_open:
        try:
            arduino_serial.close()
        except:
            pass
    arduino_serial = None
    return connect_arduino()


def send_to_arduino(value: int):
    """
    Send a single byte to Arduino.
    0 = unknown in front
    1 = known confirmed (5 sec)
    """
    global arduino_serial
    print(f"[ARDUINO] send_to_arduino called with value={value}")
    if arduino_serial is None:
        print(f"[ARDUINO] FAIL — arduino_serial is None (not connected)")
        return
    if not arduino_serial.is_open:
        print(f"[ARDUINO] FAIL — serial port is not open")
        return
    try:
        with arduino_lock:
            arduino_serial.write(bytes([value]))
        print(f"[ARDUINO] SUCCESS — sent byte {value} to Arduino")
    except Exception as e:
        print(f"[ARDUINO] ERROR sending {value}: {e}")


def arduino_reader_thread():
    """
    Background thread — continuously reads lines from Arduino.
    Arduino sends:
      "R"  when right voltage sensor (A1) triggers
      "L"  when left  voltage sensor (A0) triggers
      "CLEAR" when surveillance resumes after 20s wait
    """
    global arduino_trigger, arduino_serial, detection_state

    while True:
        if arduino_serial and arduino_serial.is_open:
            try:
                line = arduino_serial.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    print(f"Arduino says: {line}")
                    if line == "R":
                        arduino_trigger = "right"
                        detection_state["arduino_side"] = "right"
                        print("Right sensor triggered — servo stopped at 0 deg")
                    elif line == "L":
                        arduino_trigger = "left"
                        detection_state["arduino_side"] = "left"
                        print("Left sensor triggered — servo stopped at 180 deg")
                    elif line == "CLEAR":
                        arduino_trigger = "none"
                        detection_state["arduino_side"] = "none"
                        print("Arduino: surveillance resumed")
                    elif line == "RESTART":
                        # IR sensor triggered — Arduino restarted itself
                        arduino_trigger = "none"
                        arduino_reply   = 0
                        detection_state["arduino_side"] = "none"
                        detection_state["screen_red"]   = False
                        detection_state["popup"]        = False
                        print("Arduino: IR sensor triggered — system restarted")
            except Exception as e:
                print(f"Arduino read error: {e}")
                time.sleep(1)
        else:
            time.sleep(1)

# ============================================================
# FACE HELPERS
# ============================================================

def get_face_encoding(image_path):
    try:
        img  = face_recognition.load_image_file(image_path)
        locs = face_recognition.face_locations(img)
        if not locs: return None
        encs = face_recognition.face_encodings(img, locs)
        return encs[0] if encs else None
    except Exception as e:
        print("Encoding error:", e)
        return None


def get_all_members():
    """
    Returns ONLY real family members (is_unknown=False).
    Unknown faces saved by the system are intentionally excluded
    so they never match as "known" in future detections.
    """
    db = load_db()
    out = []
    for mid, m in db.get("members", {}).items():
        # Skip unknown faces — only load proper family members
        if m.get("is_unknown", False):
            continue
        if "encoding" in m:
            m2 = dict(m)
            m2["id"]       = mid
            m2["encoding"] = np.array(m2["encoding"])
            out.append(m2)
    return out


def match_face(face_encoding):
    """
    Match face against KNOWN family members only.
    Unknown records (UNK-xxxx) are never in this list,
    so they can never be returned as a "known" match.
    """
    members = get_all_members()
    if not members:
        return {"matched": False, "status": "unknown"}
    known = [m["encoding"] for m in members]
    dists = face_recognition.face_distance(known, face_encoding)
    idx   = int(np.argmin(dists))
    dist  = float(dists[idx])
    if dist <= FACE_MATCH_THRESHOLD:
        m = members[idx]
        return {"matched": True, "status": "known",
                "id": m["id"], "name": m["name"],
                "relation": m.get("relation", ""),
                "image": m.get("image", ""),
                "distance": round(dist, 4)}
    return {"matched": False, "status": "unknown", "distance": round(dist, 4)}


def save_unknown(frame, face_encoding):
    uid      = "UNK-" + str(random.randint(100000, 999999))
    img_path = save_frame_to_disk(frame, UNKNOWNS_FOLDER, uid)
    db = load_db()
    db["members"][uid] = {
        "id": uid, "name": uid, "relation": "Unknown",
        "image": "/" + img_path,
        "encoding": face_encoding.tolist(),
        "is_unknown": True,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_db(db)
    return uid, "/" + img_path


def save_alert(name, relation, image, frame, source, is_unknown=False):
    aid        = "ALT-" + str(random.randint(100000, 999999))
    frame_path = save_frame_to_disk(frame, ALERTS_FOLDER, "alert")
    db = load_db()
    db["alerts"][aid] = {
        "id": aid, "name": name, "relation": relation,
        "image": image, "frame": "/" + frame_path,
        "source": source, "is_unknown": is_unknown,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_db(db)
    return aid, "/" + frame_path

# ============================================================
# EMAIL — UNKNOWN ALERT
# ============================================================

def send_unknown_alert_email(frame_path, arduino_side):
    """
    Email sent when an UNKNOWN person is detected.
    Sends alert with detected frame image attached.
    """
    try:
        yag  = yagmail.SMTP(EMAIL_USER, EMAIL_APP_PASSWORD)
        subj = "🚨 SECURITY ALERT — Unknown Person Detected!"
        body = (
            "⚠️  HOME SECURITY ALERT\n\n"
            f"An UNKNOWN person was detected at your door.\n\n"
            f"Detection Time : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Sensor Side    : {'RIGHT side (angle 0°)' if arduino_side == 'right' else 'LEFT side (angle 180°)' if arduino_side == 'left' else 'Camera Detection'}\n\n"
            "The attached image shows the detected person.\n"
            "Please check your home immediately.\n\n"
            "— Home Security System"
        )
        if frame_path and os.path.exists(frame_path):
            yag.send(to=ALERT_EMAIL, subject=subj, contents=body, attachments=frame_path)
        else:
            yag.send(to=ALERT_EMAIL, subject=subj, contents=body)
        print("Unknown alert email sent.")
        return True
    except Exception as e:
        print(f"Unknown email error: {e}")
        return False

# ============================================================
# EMAIL — KNOWN PERSON WELCOME
# ============================================================

def send_known_person_email(name, relation, frame_path, arduino_side):
    """
    Email sent when a KNOWN family member is detected.
    Informational — not an alarm.
    """
    try:
        yag  = yagmail.SMTP(EMAIL_USER, EMAIL_APP_PASSWORD)
        subj = f"✅ Home Security — {name} has arrived"
        body = (
            f"👋  FAMILY MEMBER DETECTED\n\n"
            f"Name     : {name}\n"
            f"Relation : {relation}\n"
            f"Time     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Sensor   : {'RIGHT side (angle 0°)' if arduino_side == 'right' else 'LEFT side (angle 180°)' if arduino_side == 'left' else 'Camera Detection'}\n\n"
            f"{name} has been recognised by the Home Security System.\n\n"
            "— Home Security System"
        )
        if frame_path and os.path.exists(frame_path):
            yag.send(to=ALERT_EMAIL, subject=subj, contents=body, attachments=frame_path)
        else:
            yag.send(to=ALERT_EMAIL, subject=subj, contents=body)
        print(f"Known person email sent for {name}.")
        return True
    except Exception as e:
        print(f"Known email error: {e}")
        return False

# ============================================================
# ROUTES — PAGES
# ============================================================

@app.route("/")
def index():
    db      = load_db()
    members = []
    for mid, m in db.get("members", {}).items():
        if not m.get("is_unknown"):
            m2 = dict(m); m2["id"] = mid
            members.append(m2)
    members.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return render_template("index.html", members=members)


@app.route("/detection")
def detection():
    db     = load_db()
    alerts = list(db.get("alerts", {}).values())
    alerts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return render_template("detection.html", alerts=alerts[:20])

# ============================================================
# ROUTES — ADD / DELETE MEMBER
# ============================================================

@app.route("/add_member", methods=["POST"])
def add_member():
    name     = request.form.get("name", "").strip()
    relation = request.form.get("relation", "").strip()

    if not name or not relation:
        flash("Name and relation are required.", "danger")
        return redirect(url_for("index"))

    img_path = None

    # Case A: Camera snapshot path sent from frontend
    server_path = request.form.get("captured_server_path", "").strip()
    if server_path:
        clean = server_path.lstrip("/")
        if os.path.exists(clean):
            img_path = clean
        else:
            flash("Captured image not found. Please capture again.", "danger")
            return redirect(url_for("index"))

    # Case B: File upload
    if img_path is None:
        image = request.files.get("image")
        if not image or not image.filename:
            flash("Please upload a photo or capture from camera.", "danger")
            return redirect(url_for("index"))
        if not allowed_image(image.filename):
            flash("Only PNG, JPG, JPEG, WEBP allowed.", "danger")
            return redirect(url_for("index"))
        img_path = save_uploaded_file(image, MEMBERS_FOLDER, "member")

    # Get face encoding
    encoding = get_face_encoding(img_path)
    if encoding is None:
        if os.path.exists(img_path): os.remove(img_path)
        flash("No face detected. Please use a clear face photo.", "danger")
        return redirect(url_for("index"))

    member_id = "MEM-" + str(random.randint(100000, 999999))
    db = load_db()
    db["members"][member_id] = {
        "id": member_id, "name": name, "relation": relation,
        "image": "/" + img_path,
        "encoding": encoding.tolist(),
        "is_unknown": False,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_db(db)
    flash(f"✅ {name} added successfully!", "success")
    return redirect(url_for("index"))


@app.route("/delete_member/<member_id>", methods=["POST"])
def delete_member(member_id):
    db = load_db()
    m  = db.get("members", {}).get(member_id)
    if m:
        img = m.get("image", "").lstrip("/")
        if img and os.path.exists(img): os.remove(img)
        db["members"].pop(member_id, None)
        save_db(db)
        flash("Member removed.", "success")
    return redirect(url_for("index"))

# ============================================================
# ROUTES — CAPTURE CAMERA (Add Member page)
# ============================================================

@app.route("/capture_feed")
def capture_feed():
    global capture_cam_running
    capture_cam_running = True
    return Response(_gen_capture_frames(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/stop_capture_camera")
def stop_capture_camera():
    global capture_cam_running
    capture_cam_running = False
    return "ok"


@app.route("/take_snapshot")
def take_snapshot():
    global capture_latest_frame
    if capture_latest_frame is None:
        return jsonify({"success": False, "message": "Camera not ready. Wait a moment."})
    fname = f"snap_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}.jpg"
    path  = os.path.join(MEMBERS_FOLDER, fname).replace("\\", "/")
    ok    = cv2.imwrite(path, capture_latest_frame)
    if not ok:
        return jsonify({"success": False, "message": "Could not save image."})
    return jsonify({"success": True, "image_path": path, "image_url": "/" + path})


def _gen_capture_frames():
    global capture_cam_running, capture_latest_frame
    cap = open_camera(1)
    if cap is None:
        capture_cam_running = False
        return
    try:
        while capture_cam_running:
            ok, frame = cap.read()
            if not ok: break
            capture_latest_frame = frame.copy()
            h, w = frame.shape[:2]
            cx, cy = w // 2, h // 2
            r = min(w, h) // 4
            cv2.ellipse(frame, (cx, cy), (r, int(r * 1.3)), 0, 0, 360, (0, 212, 255), 2)
            cv2.rectangle(frame, (0, 0), (330, 46), (7, 87, 201), cv2.FILLED)
            cv2.putText(frame, "CAPTURE — position face in oval",
                        (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            ret, buf = cv2.imencode(".jpg", frame)
            if not ret: continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
    finally:
        cap.release()
        capture_cam_running = False

# ============================================================
# ROUTES — LIVE DETECTION CAMERA
# ============================================================

@app.route("/live_feed")
def live_feed():
    global live_camera_running
    live_camera_running = True
    return Response(_gen_live_frames(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/stop_live_camera")
def stop_live_camera():
    global live_camera_running
    live_camera_running = False
    return "ok"


@app.route("/detection_status")
def detection_status():
    return jsonify(detection_state)


@app.route("/dismiss_alert", methods=["POST"])
def dismiss_alert():
    detection_state.update({"screen_red": False, "popup": False})
    return jsonify({"ok": True})


@app.route("/clear_alerts", methods=["POST"])
def clear_alerts():
    db = load_db()
    db["alerts"] = {}
    save_db(db)
    return jsonify({"ok": True})


@app.route("/arduino_status")
def arduino_status():
    """
    Frontend polls this to see Arduino state.
    """
    return jsonify({
        "trigger":   arduino_trigger,
        "reply":     arduino_reply,
        "connected": arduino_serial is not None and arduino_serial.is_open,
        "port":      arduino_found_port,
    })


@app.route("/arduino_reconnect", methods=["POST"])
def arduino_reconnect():
    """
    Triggered by the Reconnect button on the detection page.
    Re-scans all ports and tries to find Arduino again.
    """
    ok = reconnect_arduino()
    return jsonify({
        "success":   ok,
        "port":      arduino_found_port,
        "connected": arduino_serial is not None and arduino_serial.is_open,
    })


def _gen_live_frames():
    """
    Main live detection loop.

    KNOWN PERSON RULE — 5 second confirmation:
    A matched face is NOT counted as known immediately.
    The person must stay visible and matched for 5 continuous
    seconds before:
      - alert is saved
      - email is sent
      - Arduino gets reply=2
      - green popup shows on frontend
    While waiting (0-5 sec), box is shown in yellow with a
    countdown so you can see it working.
    If the face disappears before 5 seconds, the timer resets.

    UNKNOWN PERSON RULE — 10 second save:
    Unknown face must stay for 10 seconds before being saved.
    """
    global live_camera_running, detection_state, arduino_reply

    cap = open_camera(1)
    if cap is None:
        live_camera_running = False
        return

    process_this    = True
    last_locs       = []
    last_names      = []
    last_colors     = []

    # Unknown person tracking
    unk_saved_ids   = {}    # face_idx -> saved unknown id (immediate on first detect)

    # Known person timers — 5 second confirmation
    # key = member_id, value = time they first appeared THIS continuous run
    known_start     = {}    # mid -> first_seen_time (resets if face disappears)
    known_confirmed = set() # mids that already passed 5 sec (alert+email done)
    email_sent_kn   = set()

    KNOWN_CONFIRM_SECS = 5  # seconds face must stay visible to count as known

    current_reply   = -1   # start at -1 so first reply (even 0) always gets sent

    try:
        while live_camera_running:
            ok, frame = cap.read()
            if not ok: break

            now = time.time()

            if process_this:
                small   = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
                rgb_s   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                locs    = face_recognition.face_locations(rgb_s)
                encs    = face_recognition.face_encodings(rgb_s, locs)

                names      = []
                colors     = []
                has_unk    = False
                new_reply  = 0

                # Track which known member IDs are visible this frame
                visible_known_ids = set()

                for i, enc in enumerate(encs):
                    res = match_face(enc)

                    # ── KNOWN PERSON (matched) ──────────────────────
                    if res["matched"]:
                        mid  = res["id"]
                        name = res["name"]
                        visible_known_ids.add(mid)

                        # Start the 5-second timer the first time we see them
                        if mid not in known_start:
                            known_start[mid] = now

                        secs_visible = now - known_start[mid]
                        secs_left    = max(0, KNOWN_CONFIRM_SECS - secs_visible)

                        if secs_visible >= KNOWN_CONFIRM_SECS:
                            # ── CONFIRMED KNOWN (5 sec passed) ──────
                            names.append(name)
                            colors.append((0, 210, 60))     # green box
                            new_reply = 1   # known confirmed 5sec → Arduino welcome

                            # Save alert once after confirmation
                            if mid not in known_confirmed:
                                known_confirmed.add(mid)

                                save_alert(
                                    name, res["relation"], res["image"],
                                    frame, "Live Camera", is_unknown=False
                                )

                                # Send email once
                                if mid not in email_sent_kn:
                                    fp = save_frame_to_disk(
                                        frame, ALERTS_FOLDER, "known")
                                    threading.Thread(
                                        target=send_known_person_email,
                                        args=(name, res["relation"], fp,
                                              detection_state.get(
                                                  "arduino_side", "none")),
                                        daemon=True
                                    ).start()
                                    email_sent_kn.add(mid)

                                # Show green popup
                                detection_state.update({
                                    "screen_red":   False,
                                    "popup":        True,
                                    "person_name":  name,
                                    "person_image": res["image"],
                                    "alert_time":   datetime.now().strftime(
                                        "%H:%M:%S"),
                                    "is_known":     True,
                                })

                        else:
                            # ── WAITING — not confirmed yet (yellow box) ─
                            label = f"{name} ({int(secs_left)+1}s...)"
                            names.append(label)
                            colors.append((0, 200, 255))    # yellow box

                    # ── UNKNOWN PERSON ──────────────────────────────
                    else:
                        has_unk = True

                        # Send 0 to Arduino EVERY frame unknown is visible
                        new_reply = 0

                        # Save unknown face once (for image reference)
                        if i not in unk_saved_ids:
                            uid, uimg = save_unknown(frame, enc)
                            unk_saved_ids[i] = uid
                        else:
                            uid  = unk_saved_ids[i]
                            uimg = detection_state.get("person_image", "")

                        # Save alert log EVERY TIME (no check)
                        save_alert(uid, "Unknown", uimg,
                                   frame, "Live Camera", is_unknown=True)

                        # Send email EVERY TIME unknown is visible
                        fp = save_frame_to_disk(
                            frame, ALERTS_FOLDER, "unk_email")
                        threading.Thread(
                            target=send_unknown_alert_email,
                            args=(fp, detection_state.get(
                                "arduino_side", "none")),
                            daemon=True
                        ).start()
                        print(f"[UNKNOWN] Email + alert + arduino=0 sent for {uid}")

                        # Update frontend state every time
                        detection_state.update({
                            "screen_red":   True,
                            "popup":        True,
                            "person_name":  "Unknown Person",
                            "person_image": uimg,
                            "alert_time":   datetime.now().strftime(
                                "%H:%M:%S"),
                            "is_known":     False,
                        })

                        uid_label = unk_saved_ids.get(i, "Unknown")
                        names.append(uid_label)
                        colors.append((0, 0, 220))          # red box

                # ── RESET known_start for members who disappeared ──
                # If a known member was being timed but is no longer
                # visible this frame, reset their timer completely.
                # They must start the 5-second count again next time.
                for mid in list(known_start.keys()):
                    if mid not in visible_known_ids:
                        del known_start[mid]
                        # Also remove from confirmed so they go through
                        # the timer again if they reappear
                        known_confirmed.discard(mid)

                # Clear red screen when no unknowns visible
                if not has_unk:
                    detection_state["screen_red"] = False
                    if not encs:
                        detection_state["popup"] = False
                        new_reply = 0

                # Send 0 every frame when unknown visible
                # Send 1 only once when known confirmed
                # Send nothing when no face
                if has_unk:
                    # Unknown visible — send 0 every single frame
                    send_to_arduino(0)
                    arduino_reply = 0
                    current_reply = 0
                    print(f"[ARDUINO] Sent 0 — unknown visible")
                elif new_reply == 1 and new_reply != current_reply:
                    # Known confirmed — send 1 once
                    send_to_arduino(1)
                    arduino_reply = 1
                    current_reply = 1
                    print(f"[ARDUINO] Sent 1 — known confirmed")
                elif not encs:
                    # No face at all
                    current_reply = -1  # reset so next detection sends fresh

                last_locs   = locs
                last_names  = names
                last_colors = colors

            process_this = not process_this

            # Draw boxes on every frame
            for (top, right, bottom, left), name, color in zip(
                    last_locs, last_names, last_colors):
                top    *= 4; right  *= 4
                bottom *= 4; left   *= 4
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                cv2.rectangle(frame, (left, bottom - 40), (right, bottom),
                              color, cv2.FILLED)
                ts = datetime.now().strftime("%H:%M:%S")
                cv2.putText(frame, f"{name}  {ts}",
                            (left + 6, bottom - 10),
                            cv2.FONT_HERSHEY_DUPLEX, 0.65, (255, 255, 255), 1)

            ret, buf = cv2.imencode(".jpg", frame)
            if not ret: continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"

    finally:
        cap.release()
        live_camera_running = False

# ============================================================
# STARTUP — Connect Arduino + Start Reader Thread
# ============================================================

def startup():
    connect_arduino()
    t = threading.Thread(target=arduino_reader_thread, daemon=True)
    t.start()

# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    startup()
    app.run(debug=False, use_reloader=False)
