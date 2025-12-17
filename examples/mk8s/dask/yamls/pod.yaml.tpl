apiVersion: v1
kind: Pod
metadata:
  name: mda-notebook
spec:
  serviceAccountName: mda-notebook-sa
  containers:
    - name: mda-notebook
      image: cr.$NEBIUS_REGION.nebius.cloud/$REGISTRY_PATH/mda-dask:latest
      env:
        - name: NEBIUS_REGION
          value: "$NEBIUS_REGION"
        - name: REGISTRY_PATH
          value: "$REGISTRY_PATH"
      command: ["bash", "-c"]
      args:
        - |
          pip install jupyterlab && \
          jupyter lab --ip=0.0.0.0 --port=8889 --no-browser --allow-root \
            --ServerApp.token=''
      volumeMounts:
        - name: my-csi-volume
          mountPath: /project
  volumes:
    - name: my-csi-volume
      persistentVolumeClaim:
        claimName: csi-pvc
