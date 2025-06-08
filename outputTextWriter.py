import os
from datetime import datetime
from pprint import pprint
from pprint import pformat

from constants import outputTextsFolder_folderPath  # My constants Python file is in .gitignore



class OutputTextWriter:

    outputTextFileName : str

    def __init__(self, inPath : str = outputTextsFolder_folderPath):
        try:
            if (os.access(inPath, os.W_OK) == False):
                raise PermissionError
        except PermissionError as e:
            raise PermissionError("outputTextWriter.py does not have permission to create/write a folder for and/or the text file in the target location ("+inPath+").")
        self.outputTextFileName : str = os.path.join(inPath, str(datetime.now())+".txt")

    def print(self, inString: str) -> str:
        print(inString)
        with open(self.outputTextFileName, "a") as textFile:
            textFile.write("\n"+inString)
        return inString

    def print_dict(self, inDict: dict, prettyPrint : bool = False) -> str:
        
        ## Pretty Print
        if (prettyPrint):

            ## Terminal
            pprint(inDict)

            ## Print to Text File
            with open(self.outputTextFileName, "a") as textFile:
                pprint(inDict, stream=textFile)

            return pformat(inDict, indent = 4)
    

        ## Regular String Printing (aka non-Pretty Print)
        else: ## Not Pretty Print
            return self.print(str(inDict))