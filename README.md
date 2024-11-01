# Eyra - AI-Powered Screen Analysis Assistant


![screenshot](https://raw.githubusercontent.com/gabrimatic/eyra/master/screenshot.png "screenshot")

Eyra is an AI-powered, interactive Python application that integrates OpenAI's vision models with system automation to deliver an enhanced screen analysis and chat experience. It combines powerful AI capabilities with automation to help you understand what's on your screen and provide real-time insights in an intuitive, interactive way.

## Overview
Eyra enables both manual and live screen analysis modes, enhancing your productivity by letting you interact with your system naturally. Whether you're conducting research, giving presentations, or working on your next project, Eyra is your intelligent companion to process and interpret visual data in real time.

## Features

- **Manual Mode**: Engage in an interactive chat where you can capture and analyze your screen or webcam images. Perfect for on-demand insights and focused interactions.

- **Live Mode**: Real-time automated screen analysis, featuring voice feedback to keep you informed. This mode is ideal for continuous monitoring or hands-free operation.

- **Cross-Platform Support**: Eyra is built to work seamlessly across macOS, Windows, and Linux environments, ensuring wide usability.

- **Text-to-Speech Integration**: Includes built-in text-to-speech functionality, allowing Eyra to communicate insights through voice in real time.

- **Image Optimization**: Automatic image processing for optimized API usage, ensuring the best performance and cost efficiency.

## Prerequisites

To run Eyra, you need the following:

- **Python 3.8 or higher**
- **An OpenAI API key**
- **System-specific dependencies**:
  - **macOS**: `imagesnap` for webcam capture.
  - **Windows**: PowerShell access for capturing screenshots.
  - **Linux**: `espeak` for text-to-speech, `paplay` for audio playback.

## Installation

### Step 1: Clone the Repository
    ```bash
    git clone https://github.com/gabrimatic/eyra.git
    cd eyra
    ```

### Step 2: Install Required Packages
    Use `pip` to install the necessary dependencies.
    ```bash
    pip install openai pillow python-dotenv
    ```

### Step 3: Set Up Environment Variables
   Create a `.env` file in the project root to store your environment variables:
    ```plaintext
    OPENAI_API_KEY=your_api_key_here
    MODEL_NAME=gpt-4o
    MAX_TOKENS=300
    IMAGE_PATH=./images/image.jpg
    USE_MOCK_CLIENT=false
    ```
   These settings allow you to configure various aspects of the application's behavior, such as model selection, token limits, and paths for image storage.

## Usage

### Run the Main Application
    ```bash
    python src/main.py
    ```

### Select Desired Mode
   - **Manual Mode**: Allows you to interact via chat commands for capturing and analyzing images from your screen or webcam.
   - **Live Mode**: Provides continuous screen analysis with voice feedback, ideal for monitoring tasks.

## Configuration

Eyra offers several configuration options to customize your experience:

- **Environment Variables**: Use the `.env` file to configure parameters like the OpenAI model, image paths, and token limits.

- **Mock Client**: If you wish to test the application without using actual API credits, enable the mock client by adding `USE_MOCK_CLIENT=true` to your `.env` file. This is especially useful during development to simulate interactions without incurring costs.

## Directory Structure

The project is organized for ease of development and maintainability:

```plaintext
src/
├── chat/           # Chat and message handling components
├── config/         # Configuration and settings
├── image/          # Image capture, optimization, and processing
├── modes/          # Application modes (Manual and Live)
└── utils/          # Utility functions and helpers
```
This structure allows a modular and clean architecture, making it easier to extend or modify functionalities in the future.

## License

[The MIT License (MIT)](https://raw.githubusercontent.com/gabrimatic/eyra/refs/heads/master/LICENCE)

## Developer
By [Hossein Yousefpour](https://gabrimatic.info "Hossein Yousefpour")

&copy; All rights reserved.

## Donate
<a href="https://www.buymeacoffee.com/gabrimatic" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Book" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a>
