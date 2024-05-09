import torch, math, time, wandb, os
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torchvision
import torchvision.transforms as transforms
from torch.utils.data import DataLoader
from tqdm import tqdm
from torchkan import KAN

# Initialize Weights & Biases
wandb.init(project="quantized_model_evaluation", entity="1ssb")

class Trainer:
    def __init__(self, model, device, train_loader, val_loader, optimizer, scheduler, criterion):
        self.model = model
        self.device = device
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.criterion = criterion

    def train_epoch(self):
        self.model.train()
        total_loss, total_accuracy = 0, 0
        for images, labels in self.train_loader:
            images, labels = images.view(-1, 28 * 28).to(self.device), labels.to(self.device)
            self.optimizer.zero_grad()
            output = self.model(images)
            loss = self.criterion(output, labels)
            loss.backward()
            self.optimizer.step()
            accuracy = (output.argmax(dim=1) == labels).float().mean().item()
            total_loss += loss.item()
            total_accuracy += accuracy
        return total_loss / len(self.train_loader), total_accuracy / len(self.train_loader)

    def validate_epoch(self):
        self.model.eval()
        val_loss, val_accuracy = 0, 0
        with torch.no_grad():
            for images, labels in self.val_loader:
                images, labels = images.view(-1, 28 * 28).to(self.device), labels.to(self.device)
                output = self.model(images)
                val_loss += self.criterion(output, labels).item()
                val_accuracy += (output.argmax(dim=1) == labels).float().mean().item()
        return val_loss / len(self.val_loader), val_accuracy / len(self.val_loader)

    def fit(self, epochs):
        train_accuracies, val_accuracies = [], []
        pbar = tqdm(range(epochs), desc="Epoch Progress")
        for epoch in pbar:
            train_loss, train_accuracy = self.train_epoch()
            val_loss, val_accuracy = self.validate_epoch()
            wandb.log({"Train Loss": train_loss, "Train Accuracy": train_accuracy, "Validation Loss": val_loss, "Validation Accuracy": val_accuracy})
            pbar.set_description(f"Epoch {epoch+1} | Train Loss: {train_loss:.4f} | Val Accuracy: {val_accuracy:.4f}")
            self.scheduler.step()
            train_accuracies.append(train_accuracy)
            val_accuracies.append(val_accuracy)
        return train_accuracies, val_accuracies

def quantize_and_evaluate(model, val_loader, criterion, save_path):
    model.cpu()  # Ensure the model is on CPU after quantization
    quantized_model = torch.quantization.quantize_dynamic(
        model,
        {nn.Linear},
        dtype=torch.qint8
    )
    quantized_model.eval()
    quantized_val_loss, quantized_val_accuracy = 0, 0
    start_time = time.time()
    with torch.no_grad():
        for images, labels in val_loader:
            # Ensure images and labels are on the CPU as well
            images, labels = images.view(-1, 28 * 28).to(torch.device('cpu')), labels.to(torch.device('cpu'))
            output = quantized_model(images)
            quantized_val_loss += criterion(output, labels).item()
            quantized_val_accuracy += (output.argmax(dim=1) == labels).float().mean().item()
    evaluation_time = time.time() - start_time
    
    # Save the quantized model to the specified path
    torch.save(quantized_model.state_dict(), save_path)
    
    return quantized_val_loss / len(val_loader), quantized_val_accuracy / len(val_loader), evaluation_time

def train_and_validate(epochs=15):
    # Data and Model Setup
    transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.5,), (0.5,))])
    trainset = torchvision.datasets.MNIST(root="./data", train=True, download=True, transform=transform)
    valset = torchvision.datasets.MNIST(root="./data", train=False, download=True, transform=transform)
    trainloader = DataLoader(trainset, batch_size=64, shuffle=True)
    valloader = DataLoader(valset, batch_size=64, shuffle=False)

    model = KAN([28 * 28, 64, 10])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

    optimizer = optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.85)
    criterion = nn.CrossEntropyLoss()

    # Training and Evaluation
    trainer = Trainer(model, device, trainloader, valloader, optimizer, scheduler, criterion)
    train_accuracies, val_accuracies = trainer.fit(epochs)

    # Save the trained model
    torch.save(model.state_dict(), "original_model.pth")

    # Quantization and Evaluation of Quantized Model
    quantized_loss, quantized_accuracy, quantized_time = quantize_and_evaluate(model, valloader, criterion, "quantized_model.pth")
    print(f"Quantized Model - Validation Loss: {quantized_loss:.4f}, Validation Accuracy: {quantized_accuracy:.4f}, Evaluation Time: {quantized_time:.4f} seconds")

    # Measure the time for evaluating the original model
    model.eval()
    start_time = time.time()
    with torch.no_grad():
        for images, labels in valloader:
            images, labels = images.view(-1, 28 * 28).to(device), labels.to(device)
            output = model(images)
    original_time = time.time() - start_time

    # Summary of results
    print(f"Original Model Evaluation Time: {original_time:.4f} seconds")
    print(f"Train Accuracies: {train_accuracies}")
    print(f"Validation Accuracies: {val_accuracies}")

    wandb.finish()

train_and_validate()