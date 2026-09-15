import { db } from "./firebase-config.js";

import {
    collection,
    addDoc,
    serverTimestamp
} from "https://www.gstatic.com/firebasejs/12.0.0/firebase-firestore.js";


export async function saveTraineeToFirebase(traineeData) {

    try {

        // Create a copy so the original localStorage data is NOT changed
        const firebaseData = { ...traineeData };

        // NEVER store password in Firestore
        delete firebaseData.password;

        const docRef = await addDoc(
            collection(db, "trainees"),
            {
                ...firebaseData,

                registrationStatus: "Pending",

                employeeVerification: {
                    status: "Pending",
                    employeeId: "",
                    employeeName: "",
                    remarks: "",
                    verifiedAt: null
                },

                providerOutcome: {
                    status: "Pending"
                },

                createdAt: serverTimestamp()
            }
        );

        console.log(
            "Trainee successfully saved to Firebase:",
            docRef.id
        );

        return docRef.id;

    } catch (error) {

        console.error(
            "Firebase trainee save failed:",
            error
        );

        return null;
    }
}