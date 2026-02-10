const express = require('express');
const { MongoClient } = require('mongodb');
const dotenv = require('dotenv');
const path = require('path');
const fs = require('fs');

dotenv.config();

const app = express();
const port = process.env.PORT || 3000;
const mongoUri = process.env.MONGODB_URI;
console.log('MongoDB URI:', mongoUri);

app.use(express.json());

// MongoDB Client
let db;
async function connectToMongo() {
    try {
        if (!mongoUri) {
            throw new Error('MONGODB_URI is not defined in .env');
        }
        const client = new MongoClient(mongoUri);
        await client.connect();
        console.log('Connected to MongoDB Atlas');

        const parsedUri = new URL(mongoUri);
        const dbName = parsedUri.pathname.replace('/', '') || 'verification_db';
        db = client.db(dbName);
        console.log(`Using database: ${dbName}`);
    } catch (error) {
        console.error('MongoDB connection error:', error);
        process.exit(1);
    }
}

// Define the expected data structure (pseudo-schema)
const verificationSchema = {
    name: String,
    uid: String,
    address: String,
    final_remark: String,
    document_type: String
};

// Store verification results from Flask with specific fields
app.post('/store-results', async (req, res) => {
    try {
        if (!db) {
            throw new Error('Database not connected');
        }

        const results = req.body;
        if (!Array.isArray(results) || results.length === 0) {
            return res.status(400).json({ error: 'Invalid or empty results' });
        }

        // Log incoming data for debugging
        console.log('Received data:', results);

        // Map results to match Flask's keys
        const formattedResults = results.map(result => ({
            name: result['name'] || '',        // Matches Flask's "name"
            uid: result['uid'] || '',          // Matches Flask's "uid"
            address: result['address'] || '',  // Matches Flask's "address"
            final_remark: result['final_remark'] || '',  // Matches Flask's "final_remark"
            document_type: result['document_type'] || '' // Matches Flask's "document_type"
        }));

        const collection = db.collection('verification_results');
        const insertResult = await collection.insertMany(formattedResults);
        
        res.status(201).json({
            message: 'Results stored successfully',
            insertedCount: insertResult.insertedCount
        });
    } catch (error) {
        console.error('Error storing results:', error);
        res.status(500).json({ error: 'Failed to store results' });
    }
});

// Retrieve all stored results
app.get('/get-results', async (req, res) => {
    try {
        if (!db) {
            throw new Error('Database not connected');
        }

        const collection = db.collection('verification_results');
        const results = await collection.find({}).toArray();
        
        res.status(200).json(results);
    } catch (error) {
        console.error('Error retrieving results:', error);
        res.status(500).json({ error: 'Failed to retrieve results' });
    }
});

// Serve the Excel file
app.get('/download-results', (req, res) => {
    const filePath = path.join(__dirname, '../uploads', 'verification_results.xlsx');
    if (fs.existsSync(filePath)) {
        res.download(filePath, 'verification_results.xlsx', (err) => {
            if (err) {
                console.error('Error downloading file:', err);
                res.status(500).json({ error: 'Failed to download file' });
            }
        });
    } else {
        res.status(404).json({ error: 'File not found' });
    }
});

// Start the server
async function startServer() {
    await connectToMongo();
    app.listen(port, () => {
        console.log(`Node.js server running on port ${port}`);
    });
}

startServer();