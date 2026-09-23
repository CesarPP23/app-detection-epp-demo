Proyecto de tesis

1. establecer la variable de entorno con el siguiente comando por cada tipo de terminal temporalmente
    powershell: $env:GOOGLE_APPLICATION_CREDENTIALS="gcp-key.json"
    cmd: set GOOGLE_APPLICATION_CREDENTIALS="gcp-key.json"
    linux/mac/raspberry: export GOOGLE_APPLICATION_CREDENTIALS="gcp-key.json" 

2. De manera permanente
    powershell: Add-Content -Path $PROFILE -Value '$env:GOOGLE_APPLICATION_CREDENTIALS="gcp-key.json"'
    cmd: setx GOOGLE_APPLICATION_CREDENTIALS "gcp-key.json"
    linux/mac/raspberry: 
        abre nano ~/.bashrc
        export GOOGLE_APPLICATION_CREDENTIALS="/ruta/a/tu/gcp-key.json"
        source ~/.bashrc