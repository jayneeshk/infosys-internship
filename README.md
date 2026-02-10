# Infosys-Internship
## Flask Practice Application 🚀

A simple Flask-based web application created to practice backend development, deployment, and Linux server operations. This project demonstrates how to set up a Flask app, run it inside a virtual environment, and deploy it on a Linux/EC2 server.

---

## 📌 Features

- Flask web application
- Virtual environment setup
- Health check script
- Linux server deployment
- Basic project structure
- Easy to extend for real-world use

---

## 🛠️ Tech Stack

- **Backend:** Python, Flask  
- **Server:** Linux / EC2  
- **Tools:** Git, Virtualenv  
- **OS:** Amazon Linux / CentOS / Ubuntu  

---

## 📂 Project Structure
flask_Practice/
│
├── app.py
├── requirements.txt
├── health_check.sh
├── venv/
├── templates/
│ └── index.html
├── static/
└── README.md

3️⃣ Update System
sudo yum update -y

or

sudo apt update && sudo apt upgrade -y

4️⃣ Install Python & Virtualenv
sudo yum install python3 -y
python3 -m venv venv

5️⃣ Activate Virtual Environment
source venv/bin/activate

6️⃣ Install Dependencies
pip install flask
pip freeze > requirements.txt

▶️ Running the Application
python app.py


Access the app in browser:

http://<server-ip>:5000

🩺 Health Check Script

Run the health check to verify application status:

./health_check.sh


If the app is running:

Application is UP


If not:

Application is DOWN

🛑 Stop the Flask Server

Press:

CTRL + C


Or find and kill the process:

ps aux | grep flask
kill -9 <PID>