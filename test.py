import smtplib

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login("your@gmail.com", "your_app_password")
    print("Login successful!")
