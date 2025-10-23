## Generate Image

### Visualizing the Simple Neural Network

Below is a diagram of the simple neural network showing the flow from the input tokens ("How are you?") through the input layer (4 neurons), hidden layer (4 neurons), and output layer (1 neuron). All connections are shown.

You can generate the new image after any changes in the dot file using Graphviz:

```bash
dot -Tpng image/simple_nn.dot -o image/simple_nn.png
```

Then, include the image in the document as follows:

```markdown
![Simple Neural Network](../image/simple_nn.png)
```

Or view the image in the image directly at: `image/simple_nn.png`

