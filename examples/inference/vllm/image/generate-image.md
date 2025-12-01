## Generate Image

You can generate the new image after any changes in the dot file using Graphviz:

```bash
dot -Tpng image/simple_nn.dot -o image/simple_nn.png
dot -Tsvg image/simple_nn.dot -o image/simple_nn.svg
dot -Tpng image/kv_attention_example.dot -o image/kv_attention_example.png
dot -Tsvg image/kv_attention_example.dot -o image/kv_attention_example.svg
```

Then, include the image in the document as follows:

```markdown
![Simple Neural Network](../image/simple_nn.png)
![Simple Neural Network](../image/kv_attention_example.png)
```


