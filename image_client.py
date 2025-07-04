"""
Standalone Image Client for Vertical Federated Learning.
Processes dermatoscopic images and communicates embeddings to the federated server.
"""

import os
import sys
import numpy as np
import tensorflow as tf
from data_loader import HAM10000DataLoader, load_and_preprocess_image
from models import create_image_encoder
from train_evaluate import (
    create_image_data_generator, 
    train_client_model,
    evaluate_client_model,
    compute_class_weights,
    extract_embeddings
)
from status import update_client_status
import pickle
import argparse
import time
from tensorflow.keras.layers import Dense, BatchNormalization, Dropout
from tensorflow.keras.models import Model

# Conditional Flask import for server mode
try:
    from flask import Flask, request, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False


class ImageClient:
    """
    Image client for vertical federated learning.
    Handles dermatoscopic image processing and embedding generation.
    """
    
    def __init__(self, client_id="image_client", data_percentage=0.1, 
                 learning_rate=0.001, embedding_dim=256):
        self.client_id = client_id
        self.data_percentage = data_percentage
        self.learning_rate = learning_rate
        self.embedding_dim = embedding_dim
        
        # Model and data
        self.encoder = None
        self.data_loader = None
        self.train_data = None
        self.val_data = None
        self.test_data = None
        
        # Metrics
        self.current_accuracy = 0.0
        self.current_f1 = 0.0
        self.current_loss = 0.0
        
        print(f"🖼️  Image Client Initialized")
        print(f"   Client ID: {self.client_id}")
        print(f"   Data percentage: {self.data_percentage*100:.1f}%")
        print(f"   Learning rate: {self.learning_rate}")
        print(f"   Embedding dimension: {self.embedding_dim}")
    
    def load_data(self, data_dir="data"):
        """Load and preprocess HAM10000 dataset for image client."""
        print(f"\n📊 Loading data for {self.client_id}...")
        
        self.data_loader = HAM10000DataLoader(data_dir=data_dir, random_state=42)
        self.data_loader.load_and_preprocess_data(data_percentage=self.data_percentage)
        
        # Get image client data
        image_data = self.data_loader.get_image_client_data()
        
        self.train_data = {
            'image_paths': image_data['train']['image_paths'],
            'labels': image_data['train']['labels'],
            'sensitive_attrs': image_data['train']['sensitive_attrs'],
            'indices': image_data['train']['indices']
        }
        
        self.val_data = {
            'image_paths': image_data['val']['image_paths'],
            'labels': image_data['val']['labels'],
            'sensitive_attrs': image_data['val']['sensitive_attrs'],
            'indices': image_data['val']['indices']
        }
        
        self.test_data = {
            'image_paths': image_data['test']['image_paths'],
            'labels': image_data['test']['labels'],
            'sensitive_attrs': image_data['test']['sensitive_attrs'],
            'indices': image_data['test']['indices']
        }
        
        print(f"   ✅ Data loaded successfully")
        print(f"   - Train samples: {len(self.train_data['image_paths'])}")
        print(f"   - Validation samples: {len(self.val_data['image_paths'])}")
        print(f"   - Test samples: {len(self.test_data['image_paths'])}")
    
    def create_model(self, use_step3_enhancements=True, use_lightweight=True):
        """
        Create the image encoder model.
        
        Args:
            use_step3_enhancements (bool): Whether to use Step 3 generalization enhancements
            use_lightweight (bool): Whether to use lightweight EfficientNetB0 vs heavy EfficientNetV2S
        """
        print("🏗️  Creating image encoder model...")
        
        # Create enhanced image encoder
        self.encoder = create_image_encoder(
            input_shape=(224, 224, 3),
            embedding_dim=self.embedding_dim,
            use_step3_enhancements=use_step3_enhancements,
            use_lightweight=use_lightweight
        )
        
        print(f"   ✅ Model created with {self.encoder.count_params():,} parameters")
    
    def train(self, epochs=10, batch_size=32, patience=3):
        """Train the image encoder model."""
        print(f"\n🎯 Training {self.client_id}...")
        print(f"   📊 Dataset: {len(self.train_data['labels'])} train, {len(self.val_data['labels'])} val samples")
        print(f"   ⚙️  Config: {epochs} epochs, batch size {batch_size}")
        
        # Prepare training data
        print(f"   📸 Loading training images...")
        train_images = self._load_images(self.train_data['image_paths'])
        train_labels = self.train_data['labels']
        
        print(f"   📸 Loading validation images...")
        val_images = self._load_images(self.val_data['image_paths'])
        val_labels = self.val_data['labels']
        
        # Compute class weights for balanced dataset
        class_weights = compute_class_weights(train_labels, method='balanced')
        print(f"   ⚖️  Class weights computed for {len(set(train_labels))} classes")
        
        # Create data generators
        train_generator = create_image_data_generator(
            self.train_data['image_paths'], 
            train_labels,
            batch_size=batch_size,
            augment=True,
            shuffle=True
        )
        
        steps_per_epoch = len(train_labels) // batch_size
        print(f"   🔄 Training setup: {steps_per_epoch} steps per epoch")
        
        # Train model with progress tracking
        print(f"   🚀 Starting training...")
        history = train_client_model(
            model=self.encoder,
            train_generator=train_generator,
            val_data=(val_images, val_labels),
            epochs=epochs,
            steps_per_epoch=steps_per_epoch,
            class_weights=class_weights,
            patience=patience,
            verbose=2  # More verbose output
        )
        
        # Update metrics
        if hasattr(history, 'history') and 'val_accuracy' in history.history:
            self.current_accuracy = max(history.history['val_accuracy'])
            self.current_loss = min(history.history['val_loss'])
            print(f"   📈 Best validation accuracy: {self.current_accuracy:.4f}")
            print(f"   📉 Best validation loss: {self.current_loss:.4f}")
        
        print(f"   ✅ Training completed successfully")
        return history
    
    def evaluate(self):
        """Evaluate the model on test data."""
        print(f"\n📊 Evaluating {self.client_id}...")
        
        test_images = self._load_images(self.test_data['image_paths'])
        test_labels = self.test_data['labels']
        
        # Evaluate model
        results = evaluate_client_model(
            model=self.encoder,
            test_data=(test_images, test_labels),
            class_names=self.data_loader.get_class_names(),
            verbose=1
        )
        
        # Update metrics
        self.current_accuracy = results['accuracy']
        self.current_f1 = results['f1_macro']
        
        # Update status
        update_client_status(
            client_id=self.client_id,
            accuracy=self.current_accuracy,
            f1_score=self.current_f1,
            loss=self.current_loss
        )
        
        return results
    
    def generate_embeddings(self, data_split='train'):
        """
        Generate embeddings for a specific data split.
        
        Args:
            data_split (str): 'train', 'val', or 'test'
        
        Returns:
            tuple: (embeddings, labels, indices)
        """
        print(f"\n🔄 Generating {data_split} embeddings...")
        
        if data_split == 'train':
            data = self.train_data
        elif data_split == 'val':
            data = self.val_data
        elif data_split == 'test':
            data = self.test_data
        else:
            raise ValueError(f"Invalid data_split: {data_split}")
        
        # Load images
        images = self._load_images(data['image_paths'])
        
        # Generate embeddings
        embeddings = extract_embeddings(self.encoder, images, batch_size=32)
        
        print(f"   ✅ Generated embeddings: {embeddings.shape}")
        
        return embeddings, data['labels'], data['indices']
    
    def _load_images(self, image_paths, batch_size=32):
        """Load and preprocess images from paths with EfficientNet preprocessing."""
        images = []
        for path in image_paths:
            img = load_and_preprocess_image(
                path, 
                target_size=(224, 224), 
                augment=False,
                use_efficientnet_preprocessing=True  # PHASE 2: Enable EfficientNet preprocessing
            )
            images.append(img)
        return np.array(images)
    
    def save_embeddings(self, embeddings, labels, indices, data_split='train', 
                       output_dir='embeddings'):
        """Save embeddings to file for server communication."""
        os.makedirs(output_dir, exist_ok=True)
        
        data = {
            'embeddings': embeddings,
            'labels': labels,
            'indices': indices,
            'client_id': self.client_id,
            'data_split': data_split,
            'embedding_dim': self.embedding_dim
        }
        
        filename = f"{output_dir}/{self.client_id}_{data_split}_embeddings.pkl"
        with open(filename, 'wb') as f:
            pickle.dump(data, f)
        
        print(f"   💾 Embeddings saved to {filename}")
        
        # Update status
        update_client_status(
            client_id=self.client_id,
            embeddings_sent=True,
            accuracy=self.current_accuracy,
            f1_score=self.current_f1
        )
    
    def load_embeddings(self, data_split='train', input_dir='embeddings'):
        """Load embeddings from file."""
        filename = f"{input_dir}/{self.client_id}_{data_split}_embeddings.pkl"
        
        if not os.path.exists(filename):
            raise FileNotFoundError(f"Embeddings file not found: {filename}")
        
        with open(filename, 'rb') as f:
            data = pickle.load(f)
        
        print(f"   📁 Embeddings loaded from {filename}")
        return data['embeddings'], data['labels'], data['indices']
    
    def save_model(self, filepath=None):
        """Save the trained model."""
        if filepath is None:
            os.makedirs('models', exist_ok=True)
            filepath = f"models/{self.client_id}_model.h5"
        
        self.encoder.save_weights(filepath)
        print(f"   💾 Model saved to {filepath}")
    
    def load_model_weights(self, filepath=None):
        """Load model weights."""
        if filepath is None:
            filepath = f"models/{self.client_id}_model.h5"
        
        if os.path.exists(filepath):
            self.encoder.load_weights(filepath)
            print(f"   📁 Model weights loaded from {filepath}")
            
            # Update status
            update_client_status(
                client_id=self.client_id,
                weights_updated=True,
                accuracy=self.current_accuracy,
                f1_score=self.current_f1
            )
        else:
            print(f"   ⚠️  Model weights file not found: {filepath}")
    
    def get_performance_metrics(self):
        """Get current performance metrics."""
        return {
            'client_id': self.client_id,
            'accuracy': self.current_accuracy,
            'f1_score': self.current_f1,
            'loss': self.current_loss
        }
    
    def load_global_model(self, round_idx):
        """Load global model weights from server for FL round."""
        fl_comm_dir = "communication"
        global_model_file = f"{fl_comm_dir}/global_model_round_{round_idx}.pkl"
        
        if os.path.exists(global_model_file):
            with open(global_model_file, 'rb') as f:
                global_data = pickle.load(f)
            
            # Apply aggregated embedding knowledge if available
            if 'aggregated_embedding_knowledge' in global_data:
                aggregated_bias = global_data['aggregated_embedding_knowledge']
                
                # Update only the bias of the final embedding layer
                current_weights = self.encoder.get_weights()
                current_weights[-1] = aggregated_bias  # Replace only bias (last layer)
                self.encoder.set_weights(current_weights)
                
                print(f"   📁 Global embedding knowledge applied for round {round_idx + 1}")
                print(f"   🔄 Updated embedding bias: {aggregated_bias.shape}")
            else:
                print(f"   📁 Global model loaded for round {round_idx + 1} (no embedding knowledge to apply)")
                
            return global_data
            
        print(f"   ⚠️  Global model file not found: {global_model_file}")
        return None
    
    # REMOVED: save_model_update method - not needed in true VFL architecture
    # VFL clients only provide embeddings, not weight updates

    def train_local_model(self, epochs=10, batch_size=16, verbose=1):
        """
        Train the local image model on client data.
        
        Args:
            epochs (int): Number of training epochs
            batch_size (int): Batch size for training
            verbose (int): Verbosity level
        
        Returns:
            dict: Training history and metrics
        """
        if not hasattr(self, 'train_data') or self.train_data is None:
            raise ValueError("No training data available. Load data first.")
        
        print(f"\n🖼️  TRAINING IMAGE CLIENT MODEL")
        print(f"   📊 Training samples: {len(self.train_data['labels'])}")
        print(f"   🔄 Epochs: {epochs}")
        print(f"   📦 Batch size: {batch_size}")
        
        # Prepare training data
        train_labels = np.array(self.train_data['labels'])
        val_labels = np.array(self.val_data['labels']) if hasattr(self, 'val_data') else None
        
        # Load images
        print(f"   📸 Loading training images...")
        train_images = self._load_images(self.train_data['image_paths'])
        val_images = None
        if hasattr(self, 'val_data') and self.val_data is not None:
            print(f"   📸 Loading validation images...")
            val_images = self._load_images(self.val_data['image_paths'])
        
        # Compute class weights for imbalanced data
        class_weights = compute_class_weights(train_labels, method='balanced')
        print(f"   ⚖️  Class weights computed for {len(set(train_labels))} classes")
        
        # Create a temporary classification model for training
        # This will be used to train the encoder, then we'll extract embeddings
        encoder_input = self.encoder.input
        encoder_output = self.encoder.output
        
        # Add classification head for training
        classifier_head = Dense(128, activation='relu', kernel_initializer='he_normal')(encoder_output)
        classifier_head = BatchNormalization()(classifier_head)
        classifier_head = Dropout(0.5)(classifier_head)
        classifier_predictions = Dense(7, activation='softmax', name='predictions')(classifier_head)
        
        # Create training model
        training_model = Model(inputs=encoder_input, outputs=classifier_predictions, name='image_training_model')
        
        # Compile with appropriate optimizer and loss
        training_model.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
            loss='sparse_categorical_crossentropy',
            metrics=['accuracy']
        )
        
        print(f"   🏗️  Training model created: {training_model.count_params():,} parameters")
        
        # Prepare validation data
        validation_data = None
        if val_images is not None and val_labels is not None:
            validation_data = (val_images, val_labels)
        
        # Train the model
        print(f"   🚀 Starting local training...")
        try:
            history = training_model.fit(
                train_images, train_labels,
                batch_size=batch_size,
                epochs=epochs,
                validation_data=validation_data,
                class_weight=class_weights,
                verbose=verbose,
                callbacks=[
                    tf.keras.callbacks.EarlyStopping(
                        monitor='val_accuracy' if validation_data else 'accuracy',
                        patience=5,
                        restore_best_weights=True
                    ),
                    tf.keras.callbacks.ReduceLROnPlateau(
                        monitor='val_loss' if validation_data else 'loss',
                        factor=0.5,
                        patience=3,
                        min_lr=1e-6
                    )
                ]
            )
            
            # Extract the trained encoder weights
            # Copy weights from training model encoder to our embedding model
            for i, layer in enumerate(self.encoder.layers):
                if i < len(training_model.layers) - 3:  # Exclude the classification head layers
                    if len(layer.get_weights()) > 0:  # Only copy layers with weights
                        layer.set_weights(training_model.layers[i].get_weights())
            
            # Generate and save fresh embeddings with trained model
            print(f"   💾 Saving fresh embeddings with trained model...")
            
            # Generate embeddings for all splits
            train_embeddings, train_labels_emb, train_indices = self.generate_embeddings('train')
            val_embeddings, val_labels_emb, val_indices = self.generate_embeddings('val')
            test_embeddings, test_labels_emb, test_indices = self.generate_embeddings('test')
            
            # Save embeddings
            self.save_embeddings(train_embeddings, train_labels_emb, train_indices, 'train')
            self.save_embeddings(val_embeddings, val_labels_emb, val_indices, 'val')
            self.save_embeddings(test_embeddings, test_labels_emb, test_indices, 'test')
            
            # Evaluate final performance
            final_train_acc = max(history.history['accuracy'])
            final_val_acc = max(history.history.get('val_accuracy', [0]))
            
            print(f"   ✅ Training completed successfully!")
            print(f"   🎯 Best training accuracy: {final_train_acc:.4f}")
            if validation_data:
                print(f"   🎯 Best validation accuracy: {final_val_acc:.4f}")
            
            return {
                'history': history.history,
                'final_train_acc': final_train_acc,
                'final_val_acc': final_val_acc,
                'epochs_completed': len(history.history['loss'])
            }
            
        except Exception as e:
            print(f"   ❌ Training failed: {str(e)}")
            return {
                'error': str(e),
                'final_train_acc': 0.0,
                'final_val_acc': 0.0,
                'epochs_completed': 0
            }


def run_fl_round(args):
    """Run a single federated learning round for image client."""
    print(f"🖼️  Image Client - FL Round {args.round_idx + 1}")
    print("=" * 50)
    
    try:
        # Create client
        client = ImageClient(
            client_id="image_client",
            data_percentage=args.data_percentage,
            learning_rate=args.learning_rate,
            embedding_dim=args.embedding_dim
        )
        
        # Load data
        client.load_data(data_dir=args.data_dir)
        
        # Create model
        client.create_model()
        
        # Load global model from server
        global_data = client.load_global_model(args.round_idx)
        
        # Train on local data
        print(f"🎯 Training on local data ({args.epochs} epochs)...")
        results = client.train(epochs=args.epochs, batch_size=args.batch_size)
        
        # Get number of training samples
        num_samples = len(client.train_data['labels'])
        
        # Save model update for server
        # VFL architecture: No weight updates needed, only embeddings
        
        print(f"✅ FL Round {args.round_idx + 1} completed")
        print(f"   📊 Local accuracy: {client.current_accuracy:.4f}")
        print(f"   📈 Local F1: {client.current_f1:.4f}")
        print(f"   📦 Samples: {num_samples}")
        
        return 0
        
    except Exception as e:
        print(f"❌ FL Round failed: {e}")
        return 1


def main():
    """Main function for standalone execution."""
    parser = argparse.ArgumentParser(description='Image Client for VFL')
    parser.add_argument('--data_dir', type=str, default='data', 
                       help='Directory containing HAM10000 dataset')
    parser.add_argument('--data_percentage', type=float, default=0.1,
                       help='Percentage of data to use (0.0-1.0)')
    parser.add_argument('--learning_rate', type=float, default=0.001,
                       help='Learning rate for training')
    parser.add_argument('--epochs', type=int, default=10,
                       help='Number of training epochs')
    parser.add_argument('--batch_size', type=int, default=32,
                       help='Batch size for training')
    parser.add_argument('--embedding_dim', type=int, default=256,
                       help='Embedding dimension')
    parser.add_argument('--output_dir', type=str, default='embeddings',
                       help='Output directory for embeddings')
    parser.add_argument('--mode', type=str, default='train', 
                       choices=['train', 'evaluate', 'generate_embeddings'],
                       help='Mode of operation')
    
    # Federated Learning arguments
    parser.add_argument('--fl_mode', type=str, default='false',
                       help='Enable federated learning mode')
    parser.add_argument('--round_idx', type=int, default=0,
                       help='Current FL round index')
    
    args = parser.parse_args()
    
    # Check for FL mode
    if args.fl_mode.lower() == 'true':
        # FL mode - participate in federated round
        return run_fl_round(args)
    
    # Create client
    client = ImageClient(
        client_id="image_client",
        data_percentage=args.data_percentage,
        learning_rate=args.learning_rate,
        embedding_dim=args.embedding_dim
    )
    
    # Load data
    client.load_data(data_dir=args.data_dir)
    
    # Create model
    client.create_model()
    
    if args.mode == 'train':
        # Train model
        client.train(epochs=args.epochs, batch_size=args.batch_size)
        
        # Evaluate model
        client.evaluate()
        
        # Save model
        client.save_model()
        
        # Generate and save embeddings for all splits
        for split in ['train', 'val', 'test']:
            embeddings, labels, indices = client.generate_embeddings(split)
            client.save_embeddings(embeddings, labels, indices, 
                                 data_split=split, output_dir=args.output_dir)
    
    elif args.mode == 'evaluate':
        # Load existing model
        client.load_model_weights()
        
        # Evaluate model
        client.evaluate()
    
    elif args.mode == 'generate_embeddings':
        # Load existing model  
        client.load_model_weights()
        
        # Generate embeddings for all splits
        for split in ['train', 'val', 'test']:
            embeddings, labels, indices = client.generate_embeddings(split)
            client.save_embeddings(embeddings, labels, indices,
                                 data_split=split, output_dir=args.output_dir)
    
    print(f"\n✅ {client.client_id} completed successfully!")


if __name__ == "__main__":
    main() 