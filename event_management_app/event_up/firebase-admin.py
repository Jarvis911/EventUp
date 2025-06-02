import firebase_admin
from firebase_admin import credentials

cred = credentials.Certificate('./event-up-36a9b-firebase-adminsdk-fbsvc-cc6040eb52.json')
firebase_admin.initialize_app(cred)