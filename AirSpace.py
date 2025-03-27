import logging
from datetime import datetime
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QVariant
from PyQt5.QtWidgets import QAction, QDialog, QVBoxLayout, QFormLayout, QLineEdit, QSpinBox, QDateEdit, QDialogButtonBox, QPushButton
from qgis.core import QgsProject, QgsPointXY, QgsFeature, QgsGeometry, QgsVectorLayer, QgsField, QgsCoordinateReferenceSystem, QgsCoordinateTransform    
from qgis.gui import QgsMapToolEmitPoint
from qgis.utils import iface
import psycopg2
import math
from .resources import *  # Ensure this import is correct for your project

# Configure logging
log_file = r"C:\Users\chris.donnelly\AppData\Roaming\QGIS\QGIS3\profiles\default\plugins\logfile.txt"
logging.basicConfig(filename=log_file, level=logging.DEBUG, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Database connection settings
DB_CONFIG = {
    "dbname": "cademo",
    "user": "postgres",
    "password": "C@supgres",
    "host": "localhost",
    "port": "5432"
}

# EPSG Code for WGS 84 (Latitude/Longitude)
TARGET_CRS = QgsCoordinateReferenceSystem("EPSG:4326")

class AirSpacePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.tool = None
        self.dlg = None
        self.point = None
        self.tif_layer = None

    def initGui(self):
        self.action = QAction(QIcon(":/icon.png"), "AirSpace", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)

    def run(self):
        # Set the map canvas CRS to EPSG:4326 (WGS 84)
        iface.mapCanvas().setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))

        self.tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.tool.canvasClicked.connect(self.onCanvasClick)
        self.iface.mapCanvas().setMapTool(self.tool)

        # Find the already loaded TIF layer by name
        self.tif_layer = self.find_tif_layer("AirspaceTIFLayer")  # Replace with your layer's name if needed

        # Don't load existing data automatically here anymore
        # self.load_existing_data()

    def find_tif_layer(self, layer_name):
        """
        Find and return the TIF layer by its name in the QGIS project.
        """
        layers = QgsProject.instance().mapLayersByName(layer_name)
        if layers:
            return layers[0]
        else:
            logging.error(f"TIF layer '{layer_name}' not found in the QGIS project.")
            return None

    def onCanvasClick(self, point, button):
        self.point = point
        self.askUserForDetails()

    def askUserForDetails(self):
        self.dlg = QDialog()
        self.dlg.setWindowTitle("Airspace Interference Point Details")

        form_layout = QFormLayout()
        self.customer = QLineEdit()
        self.object_type = QLineEdit()
        self.object_height = QSpinBox()
        self.object_height.setMaximum(3280)  # Changed from meters to feet
        self.status = QLineEdit()
        self.operations_date = QDateEdit()
        self.operations_date.setDate(datetime.today().date())
        self.duration_days = QSpinBox()
        self.duration_days.setMaximum(365)
        self.duration_hours = QSpinBox()
        self.duration_hours.setMaximum(24)

        form_layout.addRow("Customer:", self.customer)
        form_layout.addRow("Object Type:", self.object_type)
        form_layout.addRow("Object Height (ft):", self.object_height)  # Updated to feet
        form_layout.addRow("Status:", self.status)
        form_layout.addRow("Operations Date:", self.operations_date)
        form_layout.addRow("Duration (Days):", self.duration_days)
        form_layout.addRow("Duration (Hours):", self.duration_hours)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.onDialogAccepted)
        buttons.rejected.connect(self.dlg.reject)

        self.load_button = QPushButton("Load Existing Data")
        self.load_button.clicked.connect(self.load_existing_data)

        layout = QVBoxLayout()
        layout.addLayout(form_layout)
        layout.addWidget(self.load_button)
        layout.addWidget(buttons)

        self.dlg.setLayout(layout)
        self.dlg.exec_()

    def onDialogAccepted(self):
        try:
            reportid = self.customer.text()
            custinfo = self.customer.text()
            airport = "N/A"
            obj_type = self.object_type.text()
            obj_hgt = self.object_height.value()  # Already in feet
            lat = self.point.y()
            long = self.point.x()
            status = self.status.text()

            # Check for airspace clearance violation
            clearance = self.check_airspace_violation(lat, long, obj_hgt)

            self.save_to_database(reportid, custinfo, airport, obj_type, obj_hgt, lat, long, status)
            
            # Create a new feature for the map
            self.add_feature_to_layer(reportid, custinfo, airport, obj_type, obj_hgt, lat, long, status, clearance)

            # Reload existing data to refresh the layer
            self.load_existing_data()
        except Exception as e:
            logging.error(f"Error in onDialogAccepted: {str(e)}")

    def check_airspace_violation(self, lat, long, obj_hgt):
        # Fetch terrain elevation from the TIF layer at the given lat, long
        elev = self.get_terrain_elevation(QgsPointXY(long, lat))
        
        # Calculate the effective height of the object
        effective_height = obj_hgt + elev
        
        # Define the airspace clearance limit (in feet, e.g., 200 feet above the ground)
        clearance_limit = 800# Adjust this as needed for your airspace clearance requirements

        # Check if the object violates the clearance
        if effective_height > clearance_limit:
            return "Problem"
        else:
            return "No Problem"

    def get_terrain_elevation(self, point):
        """
        Fetch the terrain elevation from the TIF layer at the given point (QgsPointXY).
        """
        if self.tif_layer:
            # Convert the clicked point (which is in the map's CRS) to the TIF layer's CRS
            transform = QgsCoordinateTransform(
                iface.mapCanvas().mapSettings().destinationCrs(),  # CRS of the map canvas (EPSG:4326)
                self.tif_layer.crs(),  # CRS of the TIF layer
                QgsProject.instance()
            )
            transformed_point = transform.transform(point)

            # Get the pixel coordinates in the raster's grid
            provider = self.tif_layer.dataProvider()
            x = transformed_point.x()
            y = transformed_point.y()

            # Sample the raster at the transformed point (assuming elevation is in the first band)
            sample_value = provider.sample(QgsPointXY(x, y), 1)  # '1' indicates the first band (usually elevation)

            # If sample value is valid, return it
            if sample_value is not None:
                return sample_value[0] if isinstance(sample_value, tuple) else sample_value
            else:
                logging.error(f"Failed to get elevation at point: {transformed_point}")
                return 0 
        else:
            logging.error("TIF layer is not loaded.")
            return 0  

    def save_to_database(self, reportid, custinfo, airport, obj_type, obj_hgt, lat, long, status):
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO airspace_part77 (reportid, custinfo, airport, obj_type, obj_hgt, lat, long, status, surface)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (reportid, custinfo, airport, obj_type, obj_hgt, lat, long, status, 'Surface')  
            )
            conn.commit()
            cur.close()
            conn.close()
            logging.info("Data successfully inserted into the database.")
        except Exception as e:
            logging.error(f"Database error: {str(e)}")

    def add_feature_to_layer(self, reportid, custinfo, airport, obj_type, obj_hgt, lat, long, status, clearance):
        try:
            layer = self.get_or_create_layer()

            # Start editing the layer
            layer.startEditing()

            feature = QgsFeature()
            point = QgsPointXY(long, lat)  # lat, long
            feature.setGeometry(QgsGeometry.fromPointXY(point))

            # Map values to correct fields, including the new Clearance field
            feature.setAttributes([
                reportid, custinfo, airport, lat, long, status, 'Surface', obj_hgt, obj_type, clearance
            ])

            # Add the feature to the layer
            layer.addFeature(feature)

            # Commit changes
            layer.commitChanges()

            logging.info(f"New point added to layer: {reportid}")
        except Exception as e:
            logging.error(f"Error adding feature to layer: {str(e)}")

    def load_existing_data(self):
        try:
            # Connect to the PostgreSQL database to fetch data
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()

            # Fetch data from airspace_part77
            cur.execute(""" 
                SELECT reportid, custinfo, airport, lat, long, status, surface, obj_hgt, obj_type
                FROM airspace_part77;
            """)
            rows = cur.fetchall()

            if not rows:
                logging.warning("No data found in airspace_part77.")
                return

            # Find or create the AirspaceLayer
            layer = self.get_or_create_layer()

            # Start editing the layer
            layer.startEditing()

            # Add features to the layer
            for row in rows:
                feature = QgsFeature()
                point = QgsPointXY(row[3], row[4])  # lat, long
                feature.setGeometry(QgsGeometry.fromPointXY(point))

                # Explicitly map the values to the correct fields
                feature.setAttributes([
                    row[0],  # reportid
                    row[1],  # custinfo
                    row[2],  # airport
                    row[3],  # lat
                    row[4],  # long
                    row[5],  # status
                    row[6],  # surface
                    row[7],  # obj_hgt
                    row[8],  # obj_type
                    'No Problem'  # Default value for the Clearance field
                ])

                # Add the feature to the layer
                layer.addFeature(feature)

            # Commit changes
            layer.commitChanges()

            # Close the database connection
            cur.close()
            conn.close()
        except Exception as e:
            logging.error(f"Error loading data: {str(e)}")

    def get_or_create_layer(self):
        """
        Returns the existing AirspaceLayer or creates a new one.
        """
        layer = QgsProject.instance().mapLayersByName('AirspaceLayer')
        if layer:
            return layer[0]
        else:
            # Create a new vector layer if it doesn't exist
            layer = QgsVectorLayer("Point?crs=EPSG:4326", 'AirspaceLayer', 'memory')
            QgsProject.instance().addMapLayer(layer)
            provider = layer.dataProvider()
            provider.addAttributes([QgsField('reportid', QVariant.String),
                                    QgsField('custinfo', QVariant.String),
                                    QgsField('airport', QVariant.String),
                                    QgsField('lat', QVariant.Double),
                                    QgsField('long', QVariant.Double),
                                    QgsField('status', QVariant.String),
                                    QgsField('surface', QVariant.String),
                                    QgsField('obj_hgt', QVariant.Double),
                                    QgsField('obj_type', QVariant.String),
                                    QgsField('Clearance', QVariant.String)])  # Add 'Clearance' field
            layer.updateFields()
            return layer

    def unload(self):
        self.iface.removeToolBarIcon(self.action)

def classFactory(iface):
    return AirSpacePlugin(iface)
