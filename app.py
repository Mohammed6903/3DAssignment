import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"

import gradio as gr
import torch
import gc
from transformers import AutoTokenizer, AutoModelForCausalLM, TextIteratorStreamer
from threading import Thread
from trimesh.exchange.gltf import export_glb
import trimesh
import numpy as np
import tempfile
import sys
import subprocess

# Set an environment variable
HF_TOKEN = os.environ.get("HF_TOKEN", None)


# Check CUDA environment
def check_cuda_environment():
    """
    Print detailed information about the CUDA environment.
    """
    print("\n==== CUDA Environment Check ====")
    print(f"Python version: {sys.version}")
    print(f"PyTorch version: {torch.__version__}")

    print(f"CUDA available: {torch.cuda.is_available()}")

    if torch.cuda.is_available():
        print(f"CUDA version: {torch.version.cuda}")
        print(f"GPU count: {torch.cuda.device_count()}")
        for i in range(torch.cuda.device_count()):
            print(f"GPU {i}: {torch.cuda.get_device_name(i)}")
        print(f"Current device: {torch.cuda.current_device()}")

        print(f"GPU memory allocated: {torch.cuda.memory_allocated() / 1024**2:.2f} MB")
        print(f"GPU memory reserved: {torch.cuda.memory_reserved() / 1024**2:.2f} MB")

        try:
            x = torch.rand(100, 100).cuda()
            y = torch.rand(100, 100).cuda()
            z = torch.matmul(x, y)
            del x, y, z
            print("✅ Basic GPU tensor operations successful")
        except Exception as e:
            print(f"❌ GPU tensor operation failed: {e}")
    else:
        print(
            "❌ CUDA is not available. Check your PyTorch installation and GPU drivers."
        )

    try:
        if sys.platform == "linux":
            nvidia_smi = subprocess.check_output("nvidia-smi", shell=True)
            print("\nNVIDIA-SMI output:")
            print(nvidia_smi.decode("utf-8"))
    except:
        print("Could not run nvidia-smi. Ensure NVIDIA drivers are installed.")

    print("================================\n")


def optimize_memory():
    """
    Optimize memory usage by clearing CUDA cache and running garbage collection.
    """
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        print(f"GPU memory allocated: {torch.cuda.memory_allocated() / 1024**2:.2f} MB")
        print(f"GPU memory reserved: {torch.cuda.memory_reserved() / 1024**2:.2f} MB")


check_cuda_environment()

DESCRIPTION = """
<div>
<h1 style="text-align: center;">LLaMA-Mesh</h1>
<div>
<a style="display:inline-block" href="https://research.nvidia.com/labs/toronto-ai/LLaMA-Mesh/"><img src='https://img.shields.io/badge/public_website-8A2BE2'></a>
<a style="display:inline-block; margin-left: .5em" href="https://github.com/nv-tlabs/LLaMA-Mesh"><img src='https://img.shields.io/github/stars/nv-tlabs/LLaMA-Mesh?style=social'/></a>
</div>
<p>LLaMA-Mesh: Unifying 3D Mesh Generation with Language Models. <a style="display:inline-block" href="https://research.nvidia.com/labs/toronto-ai/LLaMA-Mesh/">[Project Page]</a> <a style="display:inline-block" href="https://github.com/nv-tlabs/LLaMA-Mesh">[Code]</a></p>
<p> Notice: (1) The default token length is 4096. If you observe incomplete generated meshes, try to increase the maximum token length into 8192.</p>
<p>(2) We only support generating a single mesh per dialog round. To generate another mesh, click the "clear" button and start a new dialog.</p>
<p>(3) If the LLM refuses to generate a 3D mesh, try adding more explicit instructions to the prompt, such as "create a 3D model of a table <strong>in OBJ format</strong>." A more effective approach is to request the mesh generation at the start of the dialog.</p>
</div>
"""

LICENSE = """
<p/>

---
Built with Meta Llama 3.1 8B
"""

PLACEHOLDER = """
<div style="padding: 30px; text-align: center; display: flex; flex-direction: column; align-items: center;">
   <h1 style="font-size: 28px; margin-bottom: 2px; opacity: 0.55;">LLaMA-Mesh</h1>
   <p style="font-size: 18px; margin-bottom: 2px; opacity: 0.65;">Create 3D meshes by chatting.</p>
</div>
"""

css = """
h1 {
  text-align: center;
  display: block;
}

#duplicate-button {
  margin: auto;
  color: white;
  background: #1565c0;
  border-radius: 100vh;
}
"""

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

print("Loading model...")
model_path = "Zhengyi/LLaMA-Mesh"
tokenizer = AutoTokenizer.from_pretrained(model_path)

if torch.cuda.is_available():
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=torch.float16,
        device_map="auto",
        low_cpu_mem_usage=True,
    )
else:
    model = AutoModelForCausalLM.from_pretrained(model_path)
    model = model.to(device)

print(f"Model loaded on: {next(model.parameters()).device}")
terminators = [tokenizer.eos_token_id, tokenizer.convert_tokens_to_ids("<|eot_id|>")]

optimize_memory()


def apply_gradient_color(mesh_text):
    """
    Apply a gradient color to the mesh vertices based on the Y-axis and save as GLB.
    Args:
        mesh_text (str): The input mesh in OBJ format as a string.
    Returns:
        str: Path to the GLB file with gradient colors applied.
    """
    temp_file = tempfile.NamedTemporaryFile(suffix=f"", delete=False).name
    with open(temp_file + ".obj", "w") as f:
        f.write(mesh_text)
    mesh = trimesh.load_mesh(temp_file + ".obj", file_type="obj")

    vertices = mesh.vertices
    y_values = vertices[:, 1]

    y_normalized = (
        (y_values - y_values.min()) / (y_values.max() - y_values.min())
        if y_values.max() > y_values.min()
        else np.zeros_like(y_values)
    )

    colors = np.zeros((len(vertices), 4))
    colors[:, 0] = y_normalized
    colors[:, 2] = 1 - y_normalized
    colors[:, 3] = 1.0

    mesh.visual.vertex_colors = colors

    glb_path = temp_file + ".glb"
    with open(glb_path, "wb") as f:
        f.write(export_glb(mesh))

    return glb_path


def visualize_mesh(mesh_text):
    """
    Convert the provided 3D mesh text into a visualizable format.
    This function assumes the input is in OBJ format.
    """
    temp_file = "temp_mesh.obj"
    with open(temp_file, "w") as f:
        f.write(mesh_text)
    return temp_file


def chat_llama3_8b(
    message: str, history: list, temperature: float, max_new_tokens: int
) -> str:
    """
    Generate a streaming response using the llama3-8b model.
    Args:
        message (str): The input message.
        history (list): The conversation history used by ChatInterface.
        temperature (float): The temperature for generating the response.
        max_new_tokens (int): The maximum number of new tokens to generate.
    Returns:
        str: The generated response.
    """
    # Print device information for debugging
    print(f"Model is on device: {next(model.parameters()).device}")

    conversation = []
    for user, assistant in history:
        conversation.extend(
            [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ]
        )
    conversation.append({"role": "user", "content": message})

    # Make sure input_ids are on the same device as the model
    input_ids = tokenizer.apply_chat_template(conversation, return_tensors="pt").to(
        next(model.parameters()).device
    )

    # Print shape of input_ids for debugging
    print(f"Input shape: {input_ids.shape}")

    # Increase timeout for more robust streaming
    streamer = TextIteratorStreamer(
        tokenizer, timeout=10.0, skip_prompt=True, skip_special_tokens=True
    )

    generate_kwargs = dict(
        input_ids=input_ids,
        streamer=streamer,
        max_new_tokens=max_new_tokens,
        do_sample=True,
        temperature=temperature,
        eos_token_id=terminators,
    )

    # This will enforce greedy generation (do_sample=False) when the temperature is passed 0
    if temperature == 0:
        generate_kwargs["do_sample"] = False

    # For debugging purposes
    print("Starting generation...")

    t = Thread(target=model.generate, kwargs=generate_kwargs)
    t.start()

    outputs = []
    try:
        for text in streamer:
            outputs.append(text)
            yield "".join(outputs)

    except Exception as e:
        print(f"Error during streaming: {e}")
        yield "".join(outputs) + "\n\n[Error during generation. Please try again.]"

    # Clean up after generation
    optimize_memory()


chatbot = gr.Chatbot(height=450, placeholder=PLACEHOLDER, label="Gradio ChatInterface")

with gr.Blocks(fill_height=True, css=css) as demo:
    with gr.Column():
        gr.Markdown(DESCRIPTION)
        with gr.Row():
            with gr.Column(scale=3):
                gr.ChatInterface(
                    fn=chat_llama3_8b,
                    chatbot=chatbot,
                    fill_height=True,
                    additional_inputs_accordion=gr.Accordion(
                        label="⚙️ Parameters", open=False, render=False
                    ),
                    additional_inputs=[
                        gr.Slider(
                            minimum=0,
                            maximum=1,
                            step=0.1,
                            value=0.95,
                            label="Temperature",
                            render=False,
                        ),
                        gr.Slider(
                            minimum=128,
                            maximum=8192,
                            step=1,
                            value=4096,
                            label="Max new tokens",
                            render=False,
                        ),
                    ],
                    examples=[
                        ["Create a 3D model of a wooden hammer"],
                        ["Create a 3D model of a pyramid in obj format"],
                        ["Create a 3D model of a cabinet."],
                        ["Create a low poly 3D model of a coffe cup"],
                        ["Create a 3D model of a table."],
                        ["Create a low poly 3D model of a tree."],
                        ["Write a python code for sorting."],
                        ["How to setup a human base on Mars? Give short answer."],
                        ["Explain theory of relativity to me like I'm 8 years old."],
                        ["What is 9,000 * 9,000?"],
                        ["Create a 3D model of a soda can."],
                        ["Create a 3D model of a sword."],
                        ["Create a 3D model of a wooden barrel"],
                        ["Create a 3D model of a chair."],
                    ],
                    cache_examples=False,
                )
                gr.Markdown(LICENSE)

            with gr.Column(scale=2):
                output_model = gr.Model3D(
                    label="3D Mesh Visualization",
                    interactive=False,
                )
                gr.Markdown(
                    "You can copy the generated 3d objects in the left and paste in the textbox below. Put the button and you will see the visualization of the 3D mesh."
                )

                mesh_input = gr.Textbox(
                    label="3D Mesh Input",
                    placeholder="Paste your 3D mesh in OBJ format here...",
                    lines=5,
                )
                visualize_button = gr.Button("Visualize 3D Mesh")

                visualize_button.click(
                    fn=apply_gradient_color, inputs=[mesh_input], outputs=[output_model]
                )

if __name__ == "__main__":
    demo.launch()
