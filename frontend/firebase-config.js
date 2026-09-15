import { initializeApp } from "https://www.gstatic.com/firebasejs/12.0.0/firebase-app.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/12.0.0/firebase-firestore.js";

const firebaseConfig = {
    apiKey: "AIzaSyDTA9U6UEzOhNy7lDKuQ-AFBMocHf-gtU8",
    authDomain: "skillbridge-70863.firebaseapp.com",
    projectId: "skillbridge-70863",
    storageBucket: "skillbridge-70863.firebasestorage.app",
    messagingSenderId: "912671486056",
    appId: "1:912671486056:web:6f2dc11ab2c6042480cbdd"
};

const app = initializeApp(firebaseConfig);

export const db = getFirestore(app);